import torch
import math
import time
import numpy as np
import os
import sys
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)

from utils.misc import spec_stream, log_csv
from utils.sampling import sample, norm_logits, max_fn

from models.cache_utils import FlashSimpleCache, GraphFlashSimpleCache, GraphFlashStreamLLMCache, GraphFlashChunkCache, GraphFlashSimpleCache, GraphFlashTopKCache, GraphFlashChunkTopKCache

from utils.misc import fake2real

@torch.inference_mode()
def Vanilla_Spec_cache(tokenizer, target, target_cache, draft, draft_cache, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None):
    # reset cache
    target_cache.reset()
    draft_cache.reset()
    
    ############ Iterative Pre-fill ############
    iter_prefill = math.ceil(input_ids.shape[1] / 100)
    for i in (range(iter_prefill)):
        outputs = target(
            input_ids=input_ids[:, i*100:(i+1)*100],
            past_key_values=target_cache,
            use_cache=True,
        )

        outputs_draft = draft(
            input_ids=input_ids[:, i*100:(i+1)*100],
            past_key_values=draft_cache,
            use_cache=True,
        )

    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    next_token = sample(norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    n = 0
    time1 = time.time()
    
    ############ Spec Decoding ############
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        pred_token_idx = next_token

        speculation_probs = []
        generated_ids = []

        for _ in range(gamma):
            outputs = draft(
                input_ids=pred_token_idx,
                past_key_values=draft_cache,
                use_cache=True,
            )

            probs = norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p)
            pred_token_idx = sample(probs)
            speculation_probs.append(probs[0])
            
            generated_ids.append(pred_token_idx.item())
            draft_count += 1

        # verification
        verify_tokens = torch.cat([next_token, torch.LongTensor([generated_ids]).to(draft.device)], dim=1)

        with torch.no_grad():
            outputs = target(
                input_ids=verify_tokens,
                past_key_values=target_cache,
                use_cache=True,
            )

        count = 0
        verify_probs = []
    
        for i in range(gamma + 1):
            assert outputs.logits.shape[1] == gamma + 1
            verify_probs.append(norm_logits(outputs.logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])

        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs[:-1]):
            r = torch.rand(1, device = draft.device)

            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i] / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(draft.device)
                if verbose:
                    spec_stream(i, tokenizer, 'green')

                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma - count
                    break

            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob-speculation_prob))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

        # if eos
        if tokenizer.eos_token_id == pred_token_idx:
            break

        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

        next_token = pred_token_idx
        
        if gamma - count > 0:
            draft_cache.seq_len -= (gamma - count) - 1
        else:
            # gamma == count, we need to update the cache for draft
            with torch.no_grad():
                outputs = draft(
                    input_ids=torch.tensor([[generated_ids[-1]]]).to(draft.device),
                    past_key_values=draft_cache,
                    use_cache=True,
                )

        target_cache.seq_len -= (gamma - count)
        assert target_cache.seq_len == draft_cache.seq_len, f"{target_cache.seq_len} != {draft_cache.seq_len}"


    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {target_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    header = "draft,target,acceptance_rate,token/s,avg_tokens,prefill,gen_len\n"
    entry = f"{draft.config._name_or_path},{target.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n}\n"

    if file_path is not None:
        log_csv(file_path, header, entry)

    return acceptance_rate


@torch.inference_mode()
def KV_Spec_cache(tokenizer, target, target_cache, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None, dataset=None, spec_args=None):
    target_cache.reset()

    iter_prefill = math.ceil(input_ids.shape[1] / 100)
    for i in range(iter_prefill):
        outputs = target(
            input_ids=input_ids[:, i*100:(i+1)*100],
            past_key_values=target_cache,
            speculation=False,
        )

    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    next_token = sample(norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))

    if hasattr(target_cache, 'update_chunk_k'):
        target_cache.update_chunk_k()
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    n = 0
    time1 = time.time()
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        pred_token_idx = next_token
        
        # speculative decoding
        speculation_probs = []
        generated_ids = []

        if hasattr(target_cache, 'update_cache'):
            target_cache.update_cache()

        for _ in range(gamma):
            outputs = target(
                input_ids=pred_token_idx,
                past_key_values=target_cache,
                speculation=True,
            )
            probs = norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p)
            pred_token_idx = sample(probs)
            
            speculation_probs.append(probs[0])
            generated_ids.append(pred_token_idx.item())

            draft_count += 1

        # verification
        verify_tokens = torch.cat([next_token, torch.LongTensor([generated_ids]).to(target.device)], dim=1)
        target_cache.seq_len -= gamma

        outputs = target(
            input_ids=verify_tokens,
            past_key_values=target_cache,
            speculation=False,
        )

        count = 0
        verify_probs = []
    
        for i in range(gamma + 1):
            verify_probs.append(norm_logits(outputs.logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])

        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs):
            r = torch.rand(1, device = target.device)
            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i] / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(target.device)
                if verbose:
                    spec_stream(i, tokenizer, 'green')
                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma - count
                    break
            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob-speculation_prob))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

            if tokenizer.eos_token_id == pred_token_idx:
                break
        
        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

        next_token = pred_token_idx
        target_cache.seq_len -= (gamma - count)
    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {target_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    header = "target,acceptance_rate,token/s,avg_tokens,prefill,gen_len,dataset\n"
    entry = f"{target.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n},{dataset}\n"
    # add sepc_args
    if spec_args is not None:
        for k, v in spec_args.items():
            header=header.replace("\n", f",{k}\n")
            entry=entry.replace("\n", f",{v}\n")

    if file_path is not None:
        log_csv(file_path, header, entry)

    return acceptance_rate

@torch.inference_mode()
def Evict_Spec_cache(tokenizer, target, target_cache, draft, draft_cache, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None, dataset=None, baseline=False):
    # reset cache
    target_cache.reset()
    draft_cache.reset()
    
    ############ Iterative Pre-fill ############
    iter_prefill = math.ceil(input_ids.shape[1] / 100)
    for i in (range(iter_prefill)):
        # print(f"prefill {i}, {iter_prefill}, {input_ids[:, i*100:(i+1)*100]}")
        outputs = target(
            input_ids=input_ids[:, i*100:(i+1)*100].to(target.device),
            past_key_values=target_cache,
        )

        draft_cache.evict(100)
        outputs_draft = draft(
            input_ids=input_ids[:, i*100:(i+1)*100].to(draft.device),
            past_key_values=draft_cache,
        )

    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    next_token = sample(norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    n = 0
    time1 = time.time()
    
    ############ Spec Decoding ############
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        pred_token_idx = next_token

        speculation_probs = []
        generated_ids = []

        draft_cache.evict(gamma + 1)

        for _ in range(gamma):
            outputs = draft(
                input_ids=pred_token_idx.to(draft.device),
                past_key_values=draft_cache,
            )

            probs = norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p)
            pred_token_idx = sample(probs)
            speculation_probs.append(probs[0])
            
            generated_ids.append(pred_token_idx.item())
            draft_count += 1

        # verification
        verify_tokens = torch.cat([next_token.to(target.device), torch.LongTensor([generated_ids]).to(target.device)], dim=1)

        with torch.no_grad():
            outputs = target(
                input_ids=verify_tokens,
                past_key_values=target_cache,
                speculation=False,
            )

        count = 0
        verify_probs = []
    
        for i in range(gamma + 1):
            assert outputs.logits.shape[1] == gamma + 1
            verify_probs.append(norm_logits(outputs.logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])


        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs[:-1]):
            r = torch.rand(1, device = draft.device)

            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i].to(draft.device) / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(draft.device)

                if verbose:
                    spec_stream(i, tokenizer, 'green')

                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma - count
                    break

            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob.to(draft.device)-speculation_prob))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

        # if eos
        if tokenizer.eos_token_id == pred_token_idx:
            break

        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

        next_token = pred_token_idx

        
        if gamma - count > 0:
            draft_cache.seq_len -= (gamma - count) - 1
        else:
            # gamma == count, we need to update the cache for draft
            with torch.no_grad():
                outputs = draft(
                    input_ids=torch.tensor([[generated_ids[-1]]]).to(draft.device),
                    past_key_values=draft_cache,
                )

        target_cache.seq_len -= (gamma - count)

    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {target_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    header = "draft,target,acceptance_rate,token/s,avg_tokens,prefill,gen_len,dataset,temperature,latency,baseline\n"
    entry = f"{draft.config._name_or_path},{target.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n},{dataset},{temperature},{(time2 - time1)/n},{baseline}\n"

    if file_path is not None:
        log_csv(file_path, header, entry)

    return acceptance_rate, (time2 - time1)/n

@torch.inference_mode()
def Graph_Evict_Spec_cache(graph_engine, tokenizer, target, target_cache, draft, draft_cache, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None, dataset=None, baseline=False):
    # reset cache
    target_cache.reset()
    draft_cache.reset()
    
    ############ Iterative Pre-fill ############
    iter_prefill = math.ceil(input_ids.shape[1] / 100)
    for i in (range(iter_prefill)):
        # print(f"prefill {i}, {iter_prefill}, {input_ids[:, i*100:(i+1)*100]}")
        outputs = target(
            input_ids=input_ids[:, i*100:(i+1)*100].to(target.device),
            past_key_values=target_cache,
        )

    _ = graph_engine.graph_draft_prefill(input_ids=input_ids)

    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    next_token = sample(norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    n = 0
    time1 = time.time()
    
    ############ Spec Decoding ############
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        pred_token_idx = next_token

        speculation_probs = []
        generated_ids = []
        
        for kk in range(gamma):
            probs = graph_engine.graph_draft_inference(input_ids=pred_token_idx, gamma_offset = kk)
            pred_token_idx = sample(probs)
            speculation_probs.append(probs)
            
            generated_ids.append(pred_token_idx.item())
            draft_count += 1

            # verification
        verify_tokens = torch.cat([next_token.to(target.device), torch.LongTensor([generated_ids]).to(target.device)], dim=1)

        with torch.no_grad():
            outputs = target(
                input_ids=verify_tokens,
                past_key_values=target_cache,
                speculation=False,
            )

        count = 0
        verify_probs = []
    
        for i in range(gamma + 1):
            assert outputs.logits.shape[1] == gamma + 1
            verify_probs.append(norm_logits(outputs.logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])


        correct_ids = []
        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs[:-1]):
            r = torch.rand(1, device = draft.device)

            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i].to(draft.device) / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(draft.device)
                correct_ids.append(i)

                if verbose:
                    spec_stream(i, tokenizer, 'green')

                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma - count
                    break

            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob.to(draft.device)-speculation_prob))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

        # if eos
        if tokenizer.eos_token_id == pred_token_idx:
            break

        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

            graph_engine.graph_draft_inference(input_ids=torch.tensor([[generated_ids[-1]]]).to(draft.device), gamma_offset = gamma)

        target_cache.seq_len -= (gamma - count)
        current_seq_len = graph_engine.engine.draft_cache.start_size + graph_engine.engine.draft_cache.recent_size + count + 1
        graph_engine.engine.draft_cache.evict_for_spec(current_seq_len)
        
        next_token = pred_token_idx

    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {target_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    header = "draft,target,acceptance_rate,token/s,avg_tokens,prefill,gen_len,dataset,temperature,latency,baseline\n"
    entry = f"{draft.config._name_or_path},{target.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n},{dataset},{temperature},{(time2 - time1)/n},{baseline}\n"

    if file_path is not None:
        log_csv(file_path, header, entry)

    return acceptance_rate, (time2 - time1)/n

@torch.inference_mode()
def Evict_Spec_Evict(tokenizer, target, target_cache, draft, draft_cache, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None, dataset=None, spec=False):
    # reset cache
    target_cache.reset()
    draft_cache.reset()
    
    ############ Iterative Pre-fill ############
    iter_prefill = math.ceil(input_ids.shape[1] / 100)
    for i in (range(iter_prefill)):
        target_cache.evict(100)
        outputs = target(
            input_ids=input_ids[:, i*100:(i+1)*100].to(target.device),
            past_key_values=target_cache,
        )

        draft_cache.evict(100)
        outputs_draft = draft(
            input_ids=input_ids[:, i*100:(i+1)*100].to(draft.device),
            past_key_values=draft_cache,
        )

    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    next_token = sample(norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    n = 0
    time1 = time.time()
    
    ############ Spec Decoding ############
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        pred_token_idx = next_token

        speculation_probs = []
        generated_ids = []

        draft_cache.evict(gamma + 1)
        target_cache.evict(gamma + 1)

        for _ in range(gamma):
            outputs = draft(
                input_ids=pred_token_idx.to(draft.device),
                past_key_values=draft_cache,
            )

            probs = norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p)
            pred_token_idx = sample(probs)
            speculation_probs.append(probs[0])
            
            generated_ids.append(pred_token_idx.item())
            draft_count += 1

        # verification
        verify_tokens = torch.cat([next_token.to(target.device), torch.LongTensor([generated_ids]).to(target.device)], dim=1)

        with torch.no_grad():
            outputs = target(
                input_ids=verify_tokens,
                past_key_values=target_cache,
                speculation=False,
            )

        count = 0
        verify_probs = []
    
        for i in range(gamma + 1):
            assert outputs.logits.shape[1] == gamma + 1
            verify_probs.append(norm_logits(outputs.logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])


        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs[:-1]):
            r = torch.rand(1, device = draft.device)

            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i].to(draft.device) / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(draft.device)

                if verbose:
                    spec_stream(i, tokenizer, 'green')

                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma - count
                    break

            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob.to(draft.device)-speculation_prob))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

        # if eos
        if tokenizer.eos_token_id == pred_token_idx:
            break

        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

        next_token = pred_token_idx

        
        if gamma - count > 0:
            draft_cache.seq_len -= (gamma - count) - 1
        else:
            # gamma == count, we need to update the cache for draft
            with torch.no_grad():
                outputs = draft(
                    input_ids=torch.tensor([[generated_ids[-1]]]).to(draft.device),
                    past_key_values=draft_cache,
                )

        target_cache.seq_len -= (gamma - count)

    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {target_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    header = "draft,target,acceptance_rate,token/s,avg_tokens,prefill,gen_len,dataset,temperature\n"
    entry = f"{draft.config._name_or_path},{target.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n},{dataset},{temperature}\n"

    if file_path is not None:
        log_csv(file_path, header, entry)

    return acceptance_rate


@torch.inference_mode()
def Evict_Spec_Stream(tokenizer, target, target_cache, draft, draft_cache, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None, dataset=None):
    # reset cache
    target_cache.reset()
    draft_cache.reset()
    
    ############ Iterative Pre-fill ############
    iter_prefill = math.ceil(input_ids.shape[1] / 100)
    for i in (range(iter_prefill)):
        outputs = target(
            input_ids=input_ids[:, i*100:(i+1)*100],
            past_key_values=target_cache,
        )

        draft_cache.evict(100)
        outputs_draft = draft(
            input_ids=input_ids[:, i*100:(i+1)*100],
            past_key_values=draft_cache,
        )

    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    next_token = sample(norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    n = 0
    time1 = time.time()
    
    ############ Spec Decoding ############
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        pred_token_idx = next_token

        speculation_probs = []
        generated_ids = []

        draft_cache.evict(gamma + 1)

        for _ in range(gamma):
            outputs = draft(
                input_ids=pred_token_idx,
                past_key_values=draft_cache,
            )

            probs = norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p)
            pred_token_idx = sample(probs)
            speculation_probs.append(probs[0])
            
            generated_ids.append(pred_token_idx.item())
            draft_count += 1

        # verification
        verify_tokens = torch.cat([next_token, torch.LongTensor([generated_ids]).to(draft.device)], dim=1)

        with torch.no_grad():
            outputs = target(
                input_ids=verify_tokens,
                past_key_values=target_cache,
            )

        count = 0
        verify_probs = []
    
        for i in range(gamma + 1):
            assert outputs.logits.shape[1] == gamma + 1
            verify_probs.append(norm_logits(outputs.logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])


        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs[:-1]):
            r = torch.rand(1, device = draft.device)

            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i] / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(draft.device)

                if verbose:
                    spec_stream(i, tokenizer, 'green')

                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma - count
                    break

            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob-speculation_prob))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

        # if eos
        if tokenizer.eos_token_id == pred_token_idx:
            break

        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

        next_token = pred_token_idx

        
        if gamma - count > 0:
            draft_cache.seq_len -= (gamma - count) - 1
        else:
            # gamma == count, we need to update the cache for draft
            with torch.no_grad():
                outputs = draft(
                    input_ids=torch.tensor([[generated_ids[-1]]]).to(draft.device),
                    past_key_values=draft_cache,
                )

        target_cache.seq_len -= (gamma - count)

    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {target_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    header = "draft,target,acceptance_rate,token/s,avg_tokens,prefill,gen_len,dataset\n"
    entry = f"{draft.config._name_or_path},{target.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n},{dataset}\n"

    if file_path is not None:
        log_csv(file_path, header, entry)

    return acceptance_rate

def shift_tensor(tensor, incoming_tensor):
    assert incoming_tensor.shape[0] == 1
    incoming_tokens = incoming_tensor.shape[1]
    if tensor.shape[-1] < 256:
        # print(tensor.shape, incoming_tensor.shape)
        tensor = torch.cat([tensor, incoming_tensor], dim=1)
    else:
        tensor[:, 1:-incoming_tokens] = tensor[:, incoming_tokens+1:].clone()
        tensor[:, -incoming_tokens:] = incoming_tensor
    return tensor

@torch.inference_mode()
def Recomp_Spec(tokenizer, target, target_cache, draft, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None, dataset=None):
    # reset cache
    target_cache.reset()
    
    ############ Iterative Pre-fill ############
    iter_prefill = math.ceil(input_ids.shape[1] / 100)
    for i in (range(iter_prefill)):
        outputs = target(
            input_ids=input_ids[:, i*100:(i+1)*100],
            past_key_values=target_cache,
        )

    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    rolling_window = input_ids[:, -256:]

    next_token = sample(norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    rolling_window = shift_tensor(rolling_window, next_token)
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    n = 0
    time1 = time.time()
    
    ############ Spec Decoding ############
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        pred_token_idx = next_token

        speculation_probs = []
        generated_ids = []

        assert gamma == 1
        # print('before revised:', rolling_window)
        for _ in range(gamma):
            outputs = draft(
                input_ids=rolling_window,
            )

            probs = norm_logits(outputs.logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p)
            pred_token_idx = sample(probs)
            speculation_probs.append(probs[0])
            
            generated_ids.append(pred_token_idx.item())
            draft_count += 1
            rolling_window = shift_tensor(rolling_window, pred_token_idx)

        # verification
        verify_tokens = torch.cat([next_token, torch.LongTensor([generated_ids]).to(draft.device)], dim=1)

        with torch.no_grad():
            outputs = target(
                input_ids=verify_tokens,
                past_key_values=target_cache,
            )

        count = 0
        verify_probs = []
    
        for i in range(gamma + 1):
            assert outputs.logits.shape[1] == gamma + 1
            verify_probs.append(norm_logits(outputs.logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])

        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs[:-1]):
            r = torch.rand(1, device = draft.device)

            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i] / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(draft.device)

                if verbose:
                    spec_stream(i, tokenizer, 'green')

                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma - count
                    break

            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob-speculation_prob))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

        # if eos
        if tokenizer.eos_token_id == pred_token_idx:
            break

        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

        next_token = pred_token_idx

        if gamma - count > 0:
            rolling_window[:,-1] = pred_token_idx
        else:
            # gamma == count, we need to update the cache for draft
            if pred_token_idx.shape == torch.Size([1]):
                pred_token_idx = pred_token_idx.unsqueeze(0)
            rolling_window = shift_tensor(rolling_window, pred_token_idx)
        # print('revised:', rolling_window)
        target_cache.seq_len -= (gamma - count)

    # exit()
    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {target_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    header = "draft,target,acceptance_rate,token/s,avg_tokens,prefill,gen_len,dataset,temperature,latency\n"
    entry = f"{draft.config._name_or_path},{target.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n},{dataset},{temperature},{(time2 - time1)/n}\n"

    if file_path is not None:
        log_csv(file_path, header, entry)

    return acceptance_rate

@torch.inference_mode()
def Graph_Spec(tokenizer, graph_engine, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None, dataset=None, spec_args=None):
    graph_engine.engine.kv_cache.reset()
    graph_engine.engine.graph_cache.reset()

    logits = graph_engine.inference(input_ids=input_ids[:,:-1])
    
    logits = graph_engine.inference(input_ids=input_ids[:,-1:]) # it can init the graph cache for GraphFlashTopKCache


    if not (isinstance(graph_engine.engine.graph_cache, GraphFlashTopKCache) or isinstance(graph_engine.engine.graph_cache, GraphFlashChunkTopKCache)):
        # graph_cache == GraphFlashStreamLLMCache, GraphFlashChunkCache
        graph_engine.init_graph_cache()


    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    next_token = sample(norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    n = 0
    time1 = time.time()
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        pred_token_idx = next_token
        
        # speculative decoding
        speculation_probs = []
        generated_ids = []

        if isinstance(graph_engine.engine.graph_cache, GraphFlashStreamLLMCache) or isinstance(graph_engine.engine.graph_cache, GraphFlashTopKCache) or isinstance(graph_engine.engine.graph_cache, GraphFlashChunkTopKCache):
            graph_engine.update_graph_cache()

        for gamma_offset in range(gamma):
            storage_ids = torch.tensor([graph_engine.engine.graph_cache.max_budget + gamma_offset], device=graph_engine.engine.model.device)
            position_ids = torch.tensor([graph_engine.engine.kv_cache.seq_len + gamma_offset], device=graph_engine.engine.model.device).unsqueeze(0)
            # print(storage_ids, position_ids, gamma_offset)
            
            # logits = graph_engine.graph_inference(input_ids=pred_token_idx, storage_ids=storage_ids, position_ids=position_ids, gamma_offset = gamma_offset)
            
            if gamma_offset == 0 and isinstance(graph_engine.engine.graph_cache, GraphFlashChunkCache):
                logits = graph_engine.graph_inference_without_capture(input_ids=pred_token_idx, storage_ids=storage_ids, position_ids=position_ids, gamma_offset = gamma_offset)
            else:
                logits = graph_engine.graph_inference(input_ids=pred_token_idx, storage_ids=storage_ids, position_ids=position_ids, gamma_offset = gamma_offset)
            
            
            # logits = graph_engine.graph_inference_without_capture(input_ids=pred_token_idx, storage_ids=storage_ids, position_ids=position_ids, gamma_offset = gamma_offset)


            probs = norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p)
            pred_token_idx = sample(probs)
            
            speculation_probs.append(probs[0])
            generated_ids.append(pred_token_idx.item())

            draft_count += 1

        # verification
        verify_tokens = torch.cat([next_token, torch.LongTensor([generated_ids]).to(graph_engine.engine.model.device)], dim=1)

        logits = graph_engine.inference(input_ids=verify_tokens)

        count = 0
        verify_probs = []
    
        for i in range(gamma + 1):
            verify_probs.append(norm_logits(logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])

        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs):
            r = torch.rand(1, device = graph_engine.engine.model.device)
            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i] / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(graph_engine.engine.model.device)
                if verbose:
                    spec_stream(i, tokenizer, 'green')
                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma - count
                    break
            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob-speculation_prob))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

            if tokenizer.eos_token_id == pred_token_idx:
                break
        
        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

        next_token = pred_token_idx
        graph_engine.engine.kv_cache.seq_len -= (gamma - count)
    
    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {graph_engine.engine.kv_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    header = "target,acceptance_rate,token/s,avg_tokens,prefill,gen_len,dataset,latency\n"
    entry = f"{graph_engine.engine.model.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n},{dataset},{(time2 - time1)/n}\n"
    # add sepc_args
    if spec_args is not None:
        for k, v in spec_args.items():
            header=header.replace("\n", f",{k}\n")
            entry=entry.replace("\n", f",{v}\n")

    if file_path is not None:
        log_csv(file_path, header, entry)

    return acceptance_rate, n/(time2 - time1)

@torch.inference_mode()
def Graph_Chain_Spec(tokenizer, graph_engine, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None, dataset=None, spec_args=None):

    # reset all cache
    graph_engine.engine.kv_cache.reset()
    graph_engine.engine.graph_cache.reset()
    graph_engine.engine.draft_cache.reset()

    logits = graph_engine.inference(input_ids=input_ids)
    _ = graph_engine.graph_draft_prefill(input_ids=input_ids)

    # init graph cache
    graph_engine.init_graph_cache()

    graph_engine.engine.kv_cache.print_status()
    graph_engine.engine.graph_cache.print_status()
    graph_engine.engine.draft_cache.print_status()

    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    next_token = sample(norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')


    n = 0
    time1 = time.time()
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        # speculative decoding for draft (68m) and streamllm 7b model
        pred_token_idx = next_token
        verify_tokens, speculation_probs = Spec_Tiny_for_streamllm(pred_token_idx, graph_engine, gamma, temperature, top_k, top_p, False, tokenizer)
        
        # print(next_token, verify_tokens)
        generated_ids = verify_tokens[1:]
        # print(generated_ids, len(speculation_probs)) gamma+2, gamma+1
        assert len(generated_ids) == len(speculation_probs), f"{len(generated_ids)} != {len(speculation_probs)} != {gamma+1}"
        draft_count += len(speculation_probs)

        gamma2 = len(generated_ids)
        
        # speculative decoding streamllm 7b model and target model
        verify_tokens = torch.cat([next_token, torch.LongTensor([generated_ids]).to(graph_engine.engine.model.device)], dim=1)
        logits = graph_engine.inference(input_ids=verify_tokens)

        count = 0
        verify_probs = []
    
        for i in range(gamma2 + 1):
            verify_probs.append(norm_logits(logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])

        # print(generated_ids, len(speculation_probs), len(verify_probs))
        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs):
            r = torch.rand(1, device = graph_engine.engine.model.device)
            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i] / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(graph_engine.engine.model.device)
                if verbose:
                    spec_stream(i, tokenizer, 'green')
                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma2 - count
                    break
            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob-speculation_prob))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

            if tokenizer.eos_token_id == pred_token_idx:
                break
        
        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

        next_token = pred_token_idx

        # update 7b cache
        graph_engine.engine.kv_cache.seq_len -= (len(generated_ids) - count)
        
        # update streamllm 7b graph cache
        graph_engine.update_graph_cache()
        
        # update streamllm 68m graph cache
        current_seq_len = graph_engine.engine.draft_cache.start_size + graph_engine.engine.draft_cache.recent_size + count
        graph_engine.engine.draft_cache.evict_for_spec(current_seq_len)

        # print(current_seq_len)
        # exit()
    
    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {graph_engine.engine.kv_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    header = "target,acceptance_rate,token/s,avg_tokens,prefill,gen_len,dataset,latency\n"
    entry = f"{graph_engine.engine.model.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n},{dataset},{(time2 - time1)/n}\n"
    # add sepc_args
    if spec_args is not None:
        for k, v in spec_args.items():
            header=header.replace("\n", f",{k}\n")
            entry=entry.replace("\n", f",{v}\n")

    if file_path is not None:
        log_csv(file_path, header, entry)

    return acceptance_rate, n / (time2 - time1)

@torch.inference_mode()
def Graph_Chain_V2(tokenizer, graph_engine, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None, dataset=None, spec_args=None):

    # reset all cache
    graph_engine.engine.kv_cache.reset()
    graph_engine.engine.graph_cache.reset()
    graph_engine.engine.draft_cache.reset()

    logits = graph_engine.inference(input_ids=input_ids)
    _ = graph_engine.graph_draft_prefill(input_ids=input_ids)

    # init graph cache
    graph_engine.init_graph_cache()

    # graph_engine.engine.kv_cache.print_status()
    # graph_engine.engine.graph_cache.print_status()
    # graph_engine.engine.draft_cache.print_status()

    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    next_token = sample(norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    acc_rate_middle_list = []
    n = 0
    time1 = time.time()
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        # speculative decoding for draft (68m) and streamllm 7b model
        pred_token_idx = next_token
        if dataset == 'password':
            verify_tokens, speculation_probs, acc_rate_middle = Spec_without_tiny_in_chain(pred_token_idx, graph_engine, gamma, False, tokenizer)
        else:
            verify_tokens, speculation_probs, acc_rate_middle = Spec_Tiny_for_streamllm_V2(pred_token_idx, graph_engine, gamma, False, tokenizer)
        acc_rate_middle_list.append(acc_rate_middle)
        # verify_tokens, speculation_probs = Spec_Tiny_for_streamllm(pred_token_idx, graph_engine, gamma, temperature, top_k, top_p, False, tokenizer)
        
        # print(next_token, verify_tokens)

        # print(len(speculation_probs), speculation_probs[0].shape)
        # exit()

        generated_ids = verify_tokens[1:]
        # print(generated_ids, len(speculation_probs)) gamma+2, gamma+1
        assert len(generated_ids) == len(speculation_probs), f"{len(generated_ids)} != {len(speculation_probs)} != {gamma+1}"
        draft_count += len(speculation_probs)

        gamma2 = len(generated_ids)
        
        # speculative decoding streamllm 7b model and target model
        verify_tokens = torch.cat([next_token, torch.LongTensor([generated_ids]).to(graph_engine.engine.model.device)], dim=1)
        logits = graph_engine.inference(input_ids=verify_tokens)

        count = 0
        verify_probs = []
    
        probs = norm_logits(logits[0], temperature=temperature ,top_k=top_k, top_p=top_p)
        for i in range(gamma2 + 1):
            verify_probs.append(probs[i])

        # for i in range(gamma2 + 1):
        #     verify_probs.append(norm_logits(logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])

        # for i in range(gamma2 + 1):
        #     verify_probs.append(torch.nn.functional.softmax(logits[:, i, :], dim=-1)[0])

        # print(generated_ids, len(speculation_probs), len(verify_probs))
        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs):
            r = torch.rand(1, device = graph_engine.engine.model.device)
            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i] / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(graph_engine.engine.model.device)
                if verbose:
                    spec_stream(i, tokenizer, 'green')
                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma2 - count
                    break
            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob-speculation_prob))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

            if tokenizer.eos_token_id == pred_token_idx:
                break
        
        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

        next_token = pred_token_idx

        # update 7b cache
        graph_engine.engine.kv_cache.seq_len -= (len(generated_ids) - count)
        
        # update streamllm 7b graph cache
        graph_engine.update_graph_cache()
        
        # update streamllm 68m graph cache
        current_seq_len = graph_engine.engine.draft_cache.start_size + graph_engine.engine.draft_cache.recent_size + count
        graph_engine.engine.draft_cache.evict_for_spec(current_seq_len)

        # print(current_seq_len)
        # exit()
    
    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {graph_engine.engine.kv_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    header = "target,acceptance_rate,token/s,avg_tokens,prefill,gen_len,dataset,acc_rate_middle,lantency\n"
    entry = f"{graph_engine.engine.model.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n},{dataset},{np.array(acc_rate_middle_list).mean()},{(time2 - time1)/n}\n"
    # add sepc_args
    if spec_args is not None:
        for k, v in spec_args.items():
            header=header.replace("\n", f",{k}\n")
            entry=entry.replace("\n", f",{v}\n")

    if file_path is not None:
        log_csv(file_path, header, entry)

    return acceptance_rate, n / (time2 - time1)

def Spec_Tiny_for_streamllm(next_token, graph_engine, gamma, temperature, top_k, top_p, verbose, tokenizer):

    n = 0
    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    pred_token_idx = next_token

    return_generated_ids = []
    return_speculation_probs = []
    return_generated_ids.append(next_token.item())


    ###### spec ########

    # time1 = time.time()
    while n < gamma:
        speculation_probs = []
        generated_ids = []

        for gamma_offset in range(n, gamma):
            storage_ids = torch.tensor([graph_engine.engine.draft_cache.start_size + graph_engine.engine.draft_cache.recent_size + gamma_offset], device=graph_engine.engine.draft.device)
            position_ids = storage_ids.clone().unsqueeze(0)
            # print(storage_ids, position_ids, gamma_offset)
            
            logits = graph_engine.graph_draft_inference(input_ids=pred_token_idx, storage_ids=storage_ids, position_ids=position_ids, gamma_offset = gamma_offset)

            probs = norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p)
            pred_token_idx = sample(probs)
            
            speculation_probs.append(probs[0])
            generated_ids.append(pred_token_idx.item())

            draft_count += 1

        # verification using streamllm 7b model
        verify_tokens = torch.cat([torch.LongTensor([return_generated_ids]).to(graph_engine.engine.model.device), torch.LongTensor([generated_ids]).to(graph_engine.engine.model.device)], dim=1)

        assert verify_tokens.shape[-1] == gamma + 1

        storage_ids = torch.arange(graph_engine.engine.graph_cache.max_budget, graph_engine.engine.graph_cache.max_budget+gamma+1, device=graph_engine.engine.model.device)
        position_ids = torch.arange(graph_engine.engine.kv_cache.seq_len, graph_engine.engine.kv_cache.seq_len+gamma+1, device=graph_engine.engine.model.device).unsqueeze(0)

        logits = graph_engine.graph_verify(input_ids=verify_tokens, storage_ids=storage_ids, position_ids=position_ids)

        count = 0
        verify_probs = []

        for i in range(n, gamma + 1):
            verify_probs.append(norm_logits(logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])

        # assert len(generated_ids) == len(speculation_probs) == len(verify_probs)-1, f"{len(generated_ids)} != {len(speculation_probs)} != {len(verify_probs)-1}"
        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs):
            # r = torch.rand(1, device = graph_engine.engine.model.device)
            # if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i] / speculation_prob[i])):
            if verify_prob[i] > speculation_prob[i]:
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(graph_engine.engine.model.device)
                return_speculation_probs.append(verify_prob)
                return_generated_ids.append(i)
                if verbose:
                    spec_stream(i, tokenizer, 'green')
            else:
                resample_count += 1
                n += 1
                # pred_token_idx = sample(max_fn(verify_prob-speculation_prob))
                pred_token_idx = sample(verify_prob)
                
                return_speculation_probs.append(verify_prob)
                return_generated_ids.append(pred_token_idx.item())
                
                break
        
        if count == len(generated_ids): # bonus sample
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            return_speculation_probs.append(verify_probs[-1])
            return_generated_ids.append(pred_token_idx.item())
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

            storage_ids = torch.tensor([graph_engine.engine.draft_cache.start_size + graph_engine.engine.draft_cache.recent_size + gamma], device=graph_engine.engine.draft.device)
            position_ids = storage_ids.clone().unsqueeze(0)
            # print(storage_ids, position_ids, gamma)
            graph_engine.graph_draft_inference(input_ids=pred_token_idx, storage_ids=storage_ids, position_ids=position_ids, gamma_offset = gamma)

        # print(n, return_generated_ids)
    
    # time2 = time.time()
    # acceptance_rate = accepted_count / draft_count
    # print("Accepted:", accepted_count, "Draft:", draft_count, "Resample:", resample_count, "Target Sample:", target_sample_count)
    # print(accepted_count / draft_count, "Acc_rate:", fake2real(accepted_count / draft_count, gamma))
    # avg_tokens = accepted_count / draft_count * gamma
    # print(f"Use {time2 - time1} sec to generate {n} tokens (now {graph_engine.engine.kv_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
    # print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    return return_generated_ids, return_speculation_probs

def Spec_Tiny_for_streamllm_V2(next_token, graph_engine, gamma, verbose, tokenizer):

    n = 0
    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    pred_token_idx = next_token

    return_generated_ids = []
    return_speculation_probs = []
    return_generated_ids.append(next_token.item())

    verify_tokens = torch.full((1, gamma + 1), 100, device=graph_engine.engine.model.device)
    verify_tokens[:, 0] = next_token

    position_ids = torch.arange(graph_engine.engine.kv_cache.seq_len, graph_engine.engine.kv_cache.seq_len+gamma+1, device=graph_engine.engine.model.device).unsqueeze(0)

    # time1 = time.time()
    while n < gamma:
        speculation_prob = graph_engine.graph_draft_inference(input_ids=verify_tokens[:,:n+1], gamma_offset = n)
        
        # print(speculation_prob.shape)
        pred_token_idx = sample(speculation_prob)
        token_idx = pred_token_idx.item()
        draft_count += 1

        verify_tokens[:, n+1:n+2] = pred_token_idx
        # print(verify_tokens)
        # print(verify_tokens, storage_ids, position_ids)
        verify_prob = graph_engine.graph_verify(input_ids=verify_tokens, position_ids=position_ids)

        # print(verify_prob.shape, speculation_prob.shape)

        r = torch.rand(1, device = graph_engine.engine.model.device)
        # print(n, verify_prob[n, token_idx],  speculation_prob[token_idx])
        if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[n, token_idx] / speculation_prob[token_idx])):
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(token_idx)
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'green')
            accepted_count += 1
            n += 1
        
            pred_token_idx = sample(verify_prob[n])
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(pred_token_idx.item())
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')
            target_sample_count += 1
            n += 1

            verify_tokens[:, n:n+1] = pred_token_idx
        
        else:
            # pred_token_idx = sample(max_fn(verify_prob[n]-speculation_prob))
            pred_token_idx = sample(verify_prob[n])
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(pred_token_idx.item())
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'red')
            resample_count += 1
            n += 1

            verify_tokens[:, n:n+1] = pred_token_idx

    # update 68m cache
    graph_engine.graph_draft_inference(input_ids=verify_tokens, gamma_offset = gamma)

    # for i in range(gamma):
    #     assert torch.allclose(return_speculation_probs[i], verify_prob[i]), i
    # exit()
    # time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    # avg_tokens = accepted_count / draft_count * gamma
    # print(f"Use {time2 - time1} sec to generate {n} tokens (now {graph_engine.engine.kv_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
    # print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")
    return return_generated_ids, return_speculation_probs, acceptance_rate


def Spec_Tiny_for_streamllm_V2_Tree(next_token, graph_engine, gamma, verbose, tokenizer):

    n = 0
    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0
    top_64_hit = 0

    pred_token_idx = next_token

    return_generated_ids = []
    return_speculation_probs = []
    return_generated_ids.append(next_token.item())

    verify_tokens = torch.full((1, gamma + 1), 100, device=graph_engine.engine.model.device)
    verify_tokens[:, 0] = next_token

    storage_ids = torch.arange(graph_engine.engine.graph_cache.max_budget, graph_engine.engine.graph_cache.max_budget+gamma+1, device=graph_engine.engine.model.device)
    position_ids = torch.arange(graph_engine.engine.kv_cache.seq_len, graph_engine.engine.kv_cache.seq_len+gamma+1, device=graph_engine.engine.model.device).unsqueeze(0)

    # time1 = time.time()
    while n < gamma:
        speculation_prob = graph_engine.graph_draft_inference(input_ids=verify_tokens[:,:n+1], gamma_offset = n)
        
        pred_token_idx = sample(speculation_prob)

        top_64_values, top_64_idx = torch.topk(speculation_prob, 64)

        token_idx = pred_token_idx.item()
        draft_count += 1

        verify_tokens[:, n+1:n+2] = pred_token_idx
        # print(verify_tokens)
        # print(verify_tokens, storage_ids, position_ids)
        verify_prob = graph_engine.graph_verify(input_ids=verify_tokens, storage_ids=storage_ids, position_ids=position_ids)

        # print(verify_prob.shape, speculation_prob.shape)

        r = torch.rand(1, device = graph_engine.engine.model.device)
        # print(n, verify_prob[n, token_idx],  speculation_prob[token_idx])

        idx = sample(verify_prob[n])
        # print(idx in top_20_idx, idx, top_20_idx)
        if idx in top_64_idx:
            top_64_hit += 1

        if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[n, token_idx] / speculation_prob[token_idx])):
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(token_idx)
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'green')
            accepted_count += 1
            n += 1
        
            pred_token_idx = sample(verify_prob[n])
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(pred_token_idx.item())
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')
            target_sample_count += 1
            n += 1

            verify_tokens[:, n:n+1] = pred_token_idx
        
        else:
            # pred_token_idx = sample(max_fn(verify_prob[n]-speculation_prob))
            pred_token_idx = sample(verify_prob[n])
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(pred_token_idx.item())
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'red')
            resample_count += 1
            n += 1

            verify_tokens[:, n:n+1] = pred_token_idx

    # update 68m cache
    graph_engine.graph_draft_inference(input_ids=verify_tokens, gamma_offset = gamma)

    # for i in range(gamma):
    #     assert torch.allclose(return_speculation_probs[i], verify_prob[i]), i
    # exit()
    # time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    # avg_tokens = accepted_count / draft_count * gamma
    # print(f"Use {time2 - time1} sec to generate {n} tokens (now {graph_engine.engine.kv_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
    # print(top_20_hit/draft_count, acceptance_rate)
    acceptance_rate = top_64_hit/draft_count
    # print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")
    return return_generated_ids, return_speculation_probs, acceptance_rate

def Spec_Tiny_for_streamllm_V3(next_token, graph_engine, gamma, verbose, tokenizer):

    # verify multiple tokens
    n = 0
    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    pred_token_idx = next_token

    return_generated_ids = []
    return_speculation_probs = []
    return_generated_ids.append(next_token.item())

    verify_tokens = torch.full((1, gamma + 2), 100, device=graph_engine.engine.model.device)
    verify_tokens[:, 0] = next_token

    storage_ids = torch.arange(graph_engine.engine.graph_cache.max_budget, graph_engine.engine.graph_cache.max_budget+gamma+1, device=graph_engine.engine.model.device)
    position_ids = torch.arange(graph_engine.engine.kv_cache.seq_len, graph_engine.engine.kv_cache.seq_len+gamma+1, device=graph_engine.engine.model.device).unsqueeze(0)

    # time1 = time.time()
    while n < gamma:
        speculation_prob = graph_engine.graph_draft_inference(input_ids=verify_tokens[:,:n+1], gamma_offset = n)
        pred_token_idx = sample(speculation_prob)
        token_idx = pred_token_idx.item()
        draft_count += 1
        verify_tokens[:, n+1:n+2] = pred_token_idx

        verify_prob = graph_engine.graph_verify(input_ids=verify_tokens[:,:-1], storage_ids=storage_ids, position_ids=position_ids)

        # print(verify_prob.shape, speculation_prob.shape)

        r = torch.rand(1, device = graph_engine.engine.model.device)
        # print(n, verify_prob[n, token_idx],  speculation_prob[token_idx])
        if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[n, token_idx] / speculation_prob[token_idx])):
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(token_idx)
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'green')
            accepted_count += 1
            n += 1
        
            pred_token_idx = sample(verify_prob[n])
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(pred_token_idx.item())
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')
            target_sample_count += 1
            n += 1
            # if n == gamma +1 :
            #     print(verify_tokens[:, n:n+1], pred_token_idx)
            verify_tokens[:, n:n+1] = pred_token_idx
        
        else:
            # pred_token_idx = sample(max_fn(verify_prob[n]-speculation_prob))
            pred_token_idx = sample(verify_prob[n])
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(pred_token_idx.item())
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'red')
            resample_count += 1
            n += 1

            verify_tokens[:, n:n+1] = pred_token_idx

    # update 68m cache
    graph_engine.graph_draft_inference(input_ids=verify_tokens[:,:n+1], gamma_offset = n)

    # for i in range(gamma):
    #     assert torch.allclose(return_speculation_probs[i], verify_prob[i]), i
    # exit()
    # time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    # avg_tokens = accepted_count / draft_count * gamma
    # print(f"Use {time2 - time1} sec to generate {n} tokens (now {graph_engine.engine.kv_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
    # print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")
    return return_generated_ids, return_speculation_probs, acceptance_rate

def Spec_without_tiny_in_chain(next_token, graph_engine, gamma, verbose, tokenizer):

    # verify multiple tokens
    n = 0
    acceptance_rate = 0
    pred_token_idx = next_token

    return_generated_ids = []
    return_speculation_probs = []
    return_generated_ids.append(next_token.item())

    verify_tokens = torch.full((1, gamma + 2), 100, device=graph_engine.engine.model.device)
    verify_tokens[:, 0] = next_token

    position_ids = torch.arange(graph_engine.engine.kv_cache.seq_len, graph_engine.engine.kv_cache.seq_len+gamma+1, device=graph_engine.engine.model.device).unsqueeze(0)

    # time1 = time.time()
    while n < gamma:
        verify_prob = graph_engine.graph_verify(input_ids=verify_tokens[:,:-1], position_ids=position_ids)
        pred_token_idx = sample(verify_prob[n])
        token_idx = pred_token_idx.item()
        return_generated_ids.append(token_idx)
        return_speculation_probs.append(verify_prob[n])
        n = n + 1
        verify_tokens[:, n:n+1] = pred_token_idx

    return return_generated_ids, return_speculation_probs, acceptance_rate


@torch.inference_mode()
def Graph_Chain_Retrieval_Spec(tokenizer, graph_engine, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None, dataset=None, spec_args=None):

    # reset all cache
    graph_engine.engine.kv_cache.reset()
    graph_engine.engine.graph_cache.reset()
    graph_engine.engine.draft_cache.reset()

    logits = graph_engine.inference(input_ids=input_ids[:,:-1])

    logits = graph_engine.inference(input_ids=input_ids[:,-1:])
    
    _ = graph_engine.graph_draft_prefill(input_ids=input_ids)

    if verbose:
        graph_engine.engine.kv_cache.print_status()
        graph_engine.engine.graph_cache.print_status()
        graph_engine.engine.draft_cache.print_status()

    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    next_token = sample(norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    acc_rate_middle_list = []
    n = 0
    time1 = time.time()
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        # speculative decoding for draft (68m) and streamllm 7b model
        pred_token_idx = next_token
        if dataset == 'password':
            verify_tokens, speculation_probs, acc_rate_middle = Spec_without_tiny_in_chain(pred_token_idx, graph_engine, gamma, False, tokenizer)
        else:
            verify_tokens, speculation_probs, acc_rate_middle = Spec_Tiny_for_streamllm_V2(pred_token_idx, graph_engine, gamma, False, tokenizer)
        acc_rate_middle_list.append(acc_rate_middle)
        # verify_tokens, speculation_probs = Spec_Tiny_for_streamllm(pred_token_idx, graph_engine, gamma, temperature, top_k, top_p, False, tokenizer)
        
        # print(next_token, verify_tokens)

        # print(len(speculation_probs), speculation_probs[0].shape)
        # exit()

        generated_ids = verify_tokens[1:]
        # print(generated_ids, len(speculation_probs)) gamma+2, gamma+1
        # assert len(generated_ids) == len(speculation_probs), f"{len(generated_ids)} != {len(speculation_probs)} != {gamma+1}"
        draft_count += len(speculation_probs)

        gamma2 = len(generated_ids)
        
        # speculative decoding streamllm 7b model and target model
        verify_tokens = torch.cat([next_token, torch.LongTensor([generated_ids]).to(graph_engine.engine.model.device)], dim=1)
        logits = graph_engine.inference(input_ids=verify_tokens)

        count = 0
        verify_probs = []
    
        probs = norm_logits(logits[0], temperature=temperature ,top_k=top_k, top_p=top_p)
        for i in range(gamma2 + 1):
            verify_probs.append(probs[i])

        # for i in range(gamma2 + 1):
        #     verify_probs.append(norm_logits(logits[:, i, :], temperature=temperature ,top_k=top_k, top_p=top_p)[0])

        # for i in range(gamma2 + 1):
        #     verify_probs.append(torch.nn.functional.softmax(logits[:, i, :], dim=-1)[0])

        # print(generated_ids, len(speculation_probs), len(verify_probs))
        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs):
            r = torch.rand(1, device = graph_engine.engine.model.device)
            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i] / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                pred_token_idx = torch.tensor([[i]]).to(graph_engine.engine.model.device)
                if verbose:
                    spec_stream(i, tokenizer, 'green')
                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma2 - count
                    break
            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob-speculation_prob))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

            if tokenizer.eos_token_id == pred_token_idx:
                break
        
        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

        next_token = pred_token_idx

        # update 7b cache
        graph_engine.engine.kv_cache.seq_len -= (len(generated_ids) - count)
        
        # update streamllm 7b graph cache
        graph_engine.update_graph_cache()
        
        # update streamllm 68m graph cache
        if dataset != 'password':
            current_seq_len = graph_engine.engine.draft_cache.start_size + graph_engine.engine.draft_cache.recent_size + count
            graph_engine.engine.draft_cache.evict_for_spec(current_seq_len)

        # if dataset == 'password' and n % 32 == 0:
        # if n % 32 == 0: #!!! should update or not?????
        if dataset == 'password' and count == 0:
            logits = graph_engine.inference(input_ids=next_token.unsqueeze(0))
            next_token = sample(norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
            if verbose:
                spec_stream(next_token[0], tokenizer, 'yellow')
            n += 1

    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {graph_engine.engine.kv_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    if file_path is not None:
        header = "target,acceptance_rate,token/s,avg_tokens,prefill,gen_len,dataset,acc_rate_middle,latency\n"
        entry = f"{graph_engine.engine.model.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n},{dataset},{np.array(acc_rate_middle_list).mean()},{(time2 - time1)/n}\n"
        # add sepc_args
        if spec_args is not None:
            for k, v in spec_args.items():
                header=header.replace("\n", f",{k}\n")
                entry=entry.replace("\n", f",{v}\n")
        log_csv(file_path, header, entry)
    # print(f"middle hit rate: {np.array(acc_rate_middle_list).mean()}")

    return acceptance_rate, n / (time2 - time1)

@torch.inference_mode()
def Baseline(tokenizer, graph_engine, input_ids, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False):
# reset all cache
    graph_engine.engine.kv_cache.reset()

    logits = graph_engine.inference(input_ids=input_ids)

    if verbose:
        graph_engine.engine.kv_cache.print_status()

    next_token = sample(norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    n = 0
    time1 = time.time()
    while n < max_len:
        logits = graph_engine.engine.model(input_ids=next_token, kv_cache=graph_engine.engine.kv_cache, graph_cache=None).logits
        # print(next_token)
        next_token = sample(norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
        n += 1
        if verbose:
            spec_stream(next_token[0], tokenizer, 'cyan')
    time2 = time.time()
    # print(n, n / (time2 - time1))
    return n / (time2 - time1)


@torch.inference_mode()
def TreeBaseline(tokenizer, graph_engine, input_ids, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False):
# reset all cache
    graph_engine.engine.kv_cache.reset()

    logits = graph_engine.prefill(input_ids=input_ids)

    if verbose:
        graph_engine.engine.kv_cache.print_status()

    next_token = sample(norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    n = 0
    time1 = time.time()
    while n < max_len:
        logits = graph_engine.engine.model(input_ids=next_token, kv_cache=graph_engine.engine.kv_cache, graph_cache=None).logits
        # print(next_token)
        next_token = sample(norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
        n += 1
        if verbose:
            spec_stream(next_token[0], tokenizer, 'cyan')
    time2 = time.time()
    # print(n, n / (time2 - time1))
    return n / (time2 - time1)

@torch.inference_mode()
def Graph_Chain_Retrieval_Spec_Recomp(tokenizer, graph_engine, input_ids, gamma=4, max_len=256, top_k=-1, top_p=0.9, temperature=0.6, verbose=False, file_path=None, dataset=None, spec_args=None):

    # reset all cache
    graph_engine.engine.kv_cache.reset()
    graph_engine.engine.graph_cache.reset()

    logits = graph_engine.inference(input_ids=input_ids[:,:-1])

    logits = graph_engine.inference(input_ids=input_ids[:,-1:])
    
    graph_engine.engine.kv_cache.print_status()
    graph_engine.engine.graph_cache.print_status()

    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    assert input_ids.shape[-1] >= 256, f"input_ids.shape[-1] < 256, {input_ids.shape[-1]}"
    rolling_window = input_ids[:, -256:]

    next_token = sample(norm_logits(logits[:,-1,:], temperature=temperature ,top_k=top_k, top_p=top_p))
    rolling_window = shift_tensor(rolling_window, next_token)
    if verbose:
        spec_stream(next_token[0], tokenizer, 'cyan')

    acc_rate_middle_list = []
    n = 0
    time1 = time.time()
    while n < max_len:
        if next_token.shape == torch.Size([1]):
            next_token = next_token.unsqueeze(0)
        
        # speculative decoding for draft (68m) and streamllm 7b model
        pred_token_idx = next_token
        # backup_rolling_window = rolling_window.clone()
        verify_tokens, speculation_probs, acc_rate_middle = Spec_Recomp_for_middle(graph_engine, gamma, False, tokenizer, rolling_window.clone())
        acc_rate_middle_list.append(acc_rate_middle)
        generated_ids = verify_tokens[1:]
        assert len(generated_ids) == len(speculation_probs), f"{len(generated_ids)} != {len(speculation_probs)} != {gamma+1}"
        draft_count += len(speculation_probs)

        gamma2 = len(generated_ids)
        
        # speculative decoding streamllm 7b model and target model
        verify_tokens = torch.cat([next_token, torch.LongTensor([generated_ids]).to(graph_engine.engine.model.device)], dim=1)
        logits = graph_engine.inference(input_ids=verify_tokens)

        count = 0
        verify_probs = []

        probs = norm_logits(logits[0], temperature=temperature ,top_k=top_k, top_p=top_p)
        for i in range(gamma2 + 1):
            verify_probs.append(probs[i])

        for i, speculation_prob, verify_prob in zip(generated_ids, speculation_probs, verify_probs):
            r = torch.rand(1, device = graph_engine.engine.model.device)
            if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[i] / speculation_prob[i])):
                count += 1
                accepted_count += 1
                n += 1
                rolling_window = shift_tensor(rolling_window, torch.tensor([[i]]).to(graph_engine.engine.model.device))
                pred_token_idx = torch.tensor([[i]]).to(graph_engine.engine.model.device)
                if verbose:
                    spec_stream(i, tokenizer, 'green')
                # if eos
                if tokenizer.eos_token_id == i:
                    draft_count -= gamma2 - count
                    break
            else:
                resample_count += 1
                n += 1
                pred_token_idx = sample(max_fn(verify_prob-speculation_prob))
                rolling_window = shift_tensor(rolling_window, pred_token_idx.unsqueeze(0))
                if verbose:
                    spec_stream(pred_token_idx, tokenizer, 'red')
                break

            if tokenizer.eos_token_id == pred_token_idx:
                break
        
        if count == len(generated_ids):
            target_sample_count += 1
            n += 1
            pred_token_idx = sample(verify_probs[-1])
            rolling_window = shift_tensor(rolling_window, pred_token_idx.unsqueeze(0))
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')

        next_token = pred_token_idx

        # update 7b cache
        graph_engine.engine.kv_cache.seq_len -= (len(generated_ids) - count)
        # print(graph_engine.engine.kv_cache.seq_len)
        
        # update streamllm 7b graph cache
        graph_engine.update_graph_cache()
    
    time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    avg_tokens = accepted_count / draft_count * gamma
    if verbose:
        print(f"Use {time2 - time1} sec to generate {n} tokens (now {graph_engine.engine.kv_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
        print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")

    header = "target,acceptance_rate,token/s,avg_tokens,prefill,gen_len,dataset,acc_rate_middle\n"
    entry = f"{graph_engine.engine.model.config._name_or_path},{acceptance_rate},{n / (time2 - time1)},{avg_tokens},{input_ids.shape[1]},{n},{dataset},{np.array(acc_rate_middle_list).mean()}\n"
    # add sepc_args
    if spec_args is not None:
        for k, v in spec_args.items():
            header=header.replace("\n", f",{k}\n")
            entry=entry.replace("\n", f",{v}\n")

    if file_path is not None:
        log_csv(file_path, header, entry)

    return acceptance_rate

def Spec_Recomp_for_middle(graph_engine, gamma, verbose, tokenizer, rolling_window):
    
    n = 0
    resample_count = 0
    accepted_count = 0
    target_sample_count = 0
    draft_count = 0

    next_token = rolling_window[:,-1]

    return_generated_ids = []
    return_speculation_probs = []
    return_generated_ids.append(next_token.item())

    verify_tokens = torch.full((1, gamma + 1), 100, device=graph_engine.engine.model.device)
    verify_tokens[:, 0] = next_token

    storage_ids = torch.arange(graph_engine.engine.graph_cache.max_budget, graph_engine.engine.graph_cache.max_budget+gamma+1, device=graph_engine.engine.model.device)
    position_ids = torch.arange(graph_engine.engine.kv_cache.seq_len, graph_engine.engine.kv_cache.seq_len+gamma+1, device=graph_engine.engine.model.device).unsqueeze(0)

    # time1 = time.time()
    while n < gamma:
        # print(rolling_window)
        speculation_prob = graph_engine.graph_draft_inference(input_ids=rolling_window)
        
        pred_token_idx = sample(speculation_prob)
        token_idx = pred_token_idx.item()
        draft_count += 1

        verify_tokens[:, n+1:n+2] = pred_token_idx
        # print(verify_tokens)
        # print(verify_tokens, storage_ids, position_ids)
        verify_prob = graph_engine.graph_verify(input_ids=verify_tokens, storage_ids=storage_ids, position_ids=position_ids)

        # print(verify_prob.shape, speculation_prob.shape)

        r = torch.rand(1, device = graph_engine.engine.model.device)
        # print(n, verify_prob[n, token_idx],  speculation_prob[token_idx])
        if r < torch.min(torch.tensor([1], device=r.device), (verify_prob[n, token_idx] / speculation_prob[token_idx])):
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(token_idx)
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'green')
            accepted_count += 1
            n += 1
            rolling_window = shift_tensor(rolling_window, pred_token_idx.unsqueeze(0))
        
            pred_token_idx = sample(verify_prob[n])
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(pred_token_idx.item())
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'blue')
            target_sample_count += 1
            n += 1
            rolling_window = shift_tensor(rolling_window, pred_token_idx.unsqueeze(0))

            verify_tokens[:, n:n+1] = pred_token_idx
        
        else:
            # pred_token_idx = sample(max_fn(verify_prob[n]-speculation_prob))
            pred_token_idx = sample(verify_prob[n])
            return_speculation_probs.append(verify_prob[n])
            return_generated_ids.append(pred_token_idx.item())
            if verbose:
                spec_stream(pred_token_idx, tokenizer, 'red')
            resample_count += 1
            n += 1
            rolling_window[:,-1] = pred_token_idx
            verify_tokens[:, n:n+1] = pred_token_idx

    # time2 = time.time()
    acceptance_rate = accepted_count / draft_count
    # avg_tokens = accepted_count / draft_count * gamma
    # print(f"Use {time2 - time1} sec to generate {n} tokens (now {graph_engine.engine.kv_cache.seq_len} tokens), Tokens/s: {n / (time2 - time1)}", flush=True)
    # print(f"accepted rate {acceptance_rate}, avg generated tokens {avg_tokens}")
    return return_generated_ids, return_speculation_probs, acceptance_rate

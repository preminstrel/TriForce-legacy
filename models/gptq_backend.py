import copy
import json
import logging
import os
from dataclasses import dataclass, field, fields
from os.path import isdir, isfile, join
from typing import Dict, List, Optional, Union

import accelerate
import huggingface_hub
import torch
import torch.nn as nn
import transformers
from accelerate.hooks import remove_hook_from_module
from safetensors import safe_open
from safetensors.torch import load_file as safe_load
from safetensors.torch import save_file as safe_save
from tqdm import tqdm
# from transformers import AutoConfig#, AutoModelForCausalLM, PreTrainedModel
from .modeling_llama import LlamaForCausalLM, PreTrainedModel, LlamaConfig
from transformers.modeling_utils import no_init_weights
from transformers.utils.generic import ContextManagers
from transformers.utils.hub import (
    CommitOperationAdd,
    PushToHubMixin,
    cached_file,
    create_commit,
    create_repo,
)

from auto_gptq.nn_modules._fused_base import FusedBaseAttentionModule, FusedBaseMLPModule
from auto_gptq.nn_modules.qlinear import GeneralQuantLinear
from auto_gptq.quantization import GPTQ
from auto_gptq.utils.data_utils import collate_data
from auto_gptq.utils.import_utils import (
    AUTOGPTQ_CUDA_AVAILABLE,
    EXLLAMA_KERNELS_AVAILABLE,
    EXLLAMAV2_KERNELS_AVAILABLE,
    MARLIN_AVAILABLE,
    QIGEN_AVAILABLE,
    TRITON_AVAILABLE,
    dynamically_import_QuantLinear,
)
from auto_gptq.utils.marlin_utils import (
    _validate_marlin_compatibility,
    _validate_marlin_device_support,
    prepare_model_for_marlin_load,
)
from auto_gptq.modeling._const import CPU, CUDA_0, SUPPORTED_MODELS
from auto_gptq.modeling._utils import (
    autogptq_post_init,
    find_layers,
    get_device,
    get_module_by_name_prefix,
    get_module_by_name_suffix,
    make_quant,
    make_sure_no_tensor_in_meta_device,
    move_to_device,
    pack_from_tensors,
    pack_model,
    preprocess_checkpoint_qigen,
    simple_dispatch_model,
    unpack_awq,
)


logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
formatter = logging.Formatter("%(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

SYNONYMS = {
    "w_bit": "bits",
    "q_group_size": "group_size",
}


@dataclass
class BaseQuantizeConfig(PushToHubMixin):
    bits: int = field(default=4, metadata={"choices": [2, 3, 4, 8]})
    group_size: int = field(default=-1)
    damp_percent: float = field(default=0.01)
    desc_act: bool = field(default=True)
    static_groups: bool = field(default=False)
    sym: bool = field(default=True)
    true_sequential: bool = field(default=True)
    is_marlin_format: bool = field(default=False)
    model_name_or_path: Optional[str] = field(default=None)
    model_file_base_name: Optional[str] = field(default=None)
    awq_gemm_checkpoint: Optional[bool] = field(default=False)

    def __post_init__(self):
        fields_info = fields(self)

        if self.bits not in fields_info[0].metadata["choices"]:
            raise ValueError(f"only support quantize to {fields_info[0].metadata['choices']} bits.")
        if self.group_size != -1 and self.group_size <= 0:
            raise ValueError("unless equal to -1, group_size must greater then 0.")
        if not (0 < self.damp_percent < 1):
            raise ValueError("damp_percent must between 0 and 1.")

    def save_pretrained(self, save_dir: str, **kwargs):
        with open(join(save_dir, "quantize_config.json"), "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_pretrained(cls, save_dir: str, **kwargs):
        # Parameters related to loading from Hugging Face Hub
        cache_dir = kwargs.pop("cache_dir", None)
        force_download = kwargs.pop("force_download", False)
        resume_download = kwargs.pop("resume_download", False)
        proxies = kwargs.pop("proxies", None)
        local_files_only = kwargs.pop("local_files_only", False)
        use_auth_token = kwargs.pop("use_auth_token", None)
        revision = kwargs.pop("revision", None)
        subfolder = kwargs.pop("subfolder", None)
        commit_hash = kwargs.pop("_commit_hash", None)

        transformers_config = False
        for quantize_config_filename in [
            "quantize_config.json",
            "quant_config.json",
            "config.json",
        ]:
            if os.path.isdir(save_dir):  # Local
                resolved_config_file = join(save_dir, quantize_config_filename)
            else:  # Remote
                resolved_config_file = cached_file(
                    save_dir,
                    quantize_config_filename,
                    cache_dir=cache_dir,
                    force_download=force_download,
                    resume_download=resume_download,
                    proxies=proxies,
                    use_auth_token=use_auth_token,
                    revision=revision,
                    local_files_only=local_files_only,
                    subfolder=subfolder,
                    _raise_exceptions_for_missing_entries=False,
                    _raise_exceptions_for_connection_errors=False,
                    _commit_hash=commit_hash,
                )
            if resolved_config_file is not None:
                if quantize_config_filename == "config.json":
                    transformers_config = True
                break

        if resolved_config_file is None:
            raise ValueError(
                "No quantize_config.json, quant_config.json or config.json file was found in the model repository."
            )

        field_names = [field.name for field in fields(cls)]
        with open(resolved_config_file, "r", encoding="utf-8") as f:
            args_from_json = json.load(f)

            if transformers_config:
                args_from_json = args_from_json["quantization_config"]

            filtered_args = {"awq_gemm_checkpoint": False}
            for key, val in args_from_json.items():
                if key == "version" and val == "GEMM":
                    filtered_args["awq_gemm_checkpoint"] = True
                elif key in field_names:
                    filtered_args[key] = val
                elif key in SYNONYMS and SYNONYMS[key] in field_names:
                    filtered_args[SYNONYMS[key]] = val
                else:
                    logger.warning(f"ignoring unknown parameter in {quantize_config_filename}: {key}.")

            if filtered_args["awq_gemm_checkpoint"]:
                # AWQ does not reorder the rows.
                filtered_args["desc_act"] = False

            if "sym" not in args_from_json:
                logger.warning(
                    f"The quantization configuration {quantize_config_filename} does not contain an entry `sym` (symetric quantization). This may result in silent errors."
                )

            return cls(**filtered_args)

    def to_dict(self):
        return {
            "bits": self.bits,
            "group_size": self.group_size,
            "damp_percent": self.damp_percent,
            "desc_act": self.desc_act,
            "static_groups": self.static_groups,
            "sym": self.sym,
            "true_sequential": self.true_sequential,
            "model_name_or_path": self.model_name_or_path,
            "model_file_base_name": self.model_file_base_name,
            "is_marlin_format": self.is_marlin_format,
            "quant_method": "gptq",
        }


class BaseGPTQForCausalLM(nn.Module, PushToHubMixin):
    layer_type: str = None
    layers_block_name: str = None
    outside_layer_modules: List[str] = None
    inside_layer_modules: List[List[str]] = None
    lm_head_name: str = "lm_head"

    fused_attn_module_type: Optional[FusedBaseAttentionModule] = None
    fused_mlp_module_type: Optional[FusedBaseMLPModule] = None

    def __init__(
        self,
        model: PreTrainedModel,
        quantized: bool,
        quantize_config: BaseQuantizeConfig,
        is_triton_backend: bool = False,
        injected_fused_attention: bool = False,
        injected_fused_mlp: bool = False,
        trainable: bool = False,
    ):
        super().__init__()

        self.model = model
        self.model_type = self.model.config.model_type
        self._quantized = quantized
        self.quantize_config = quantize_config
        self.config = self.model.config

        self.is_triton_backend = is_triton_backend
        self.injected_fused_attention = injected_fused_attention
        self.injected_fused_mlp = injected_fused_mlp
        self.trainable = trainable

    @property
    def quantized(self):
        return self._quantized

    @property
    def hf_device_map(self):
        return getattr(self.model, "hf_device_map", None)

    @property
    def device(self):
        if not self.hf_device_map:
            return self.model.device
        else:
            device = [d for d in self.hf_device_map.values() if d not in {"disk"}][0]
            return torch.device(device)

    def to(self, device: Union[str, torch.device]):
        self.model.to(device)
        return self

    def forward(self, *args, **kwargs):
        return self.model(*args, **kwargs)

    @classmethod
    def from_quantized(
        cls,
        model_name_or_path: Optional[str],
        device_map: Optional[Union[str, Dict[str, Union[int, str]]]] = None,
        max_memory: Optional[dict] = None,
        device: Optional[Union[str, int]] = None,
        low_cpu_mem_usage: bool = False,
        use_triton: bool = False,
        use_qigen: bool = False,
        use_marlin: bool = False,
        torch_dtype: Optional[torch.dtype] = None,
        inject_fused_attention: bool = True,
        inject_fused_mlp: bool = True,
        use_cuda_fp16: bool = True,
        quantize_config: Optional[BaseQuantizeConfig] = None,
        model_basename: Optional[str] = None,
        use_safetensors: bool = True,
        trust_remote_code: bool = False,
        warmup_triton: bool = False,
        trainable: bool = False,
        disable_exllama: Optional[bool] = None,
        disable_exllamav2: bool = False,
        **kwargs,
    ):
        """load quantized model from local disk"""
        # If disable_exllamav2 is True, we want to fall back on the exllama kernel and not the cuda/cuda_old ones.
        if disable_exllama is None:
            if disable_exllamav2:
                disable_exllama = False
            else:
                disable_exllama = True

        # Parameters related to loading from Hugging Face Hub
        cache_dir = kwargs.pop("cache_dir", None)
        force_download = kwargs.pop("force_download", False)
        resume_download = kwargs.pop("resume_download", False)
        proxies = kwargs.pop("proxies", None)
        local_files_only = kwargs.pop("local_files_only", False)
        use_auth_token = kwargs.pop("use_auth_token", None)
        revision = kwargs.pop("revision", None)
        subfolder = kwargs.pop("subfolder", "")
        commit_hash = kwargs.pop("_commit_hash", None)

        cached_file_kwargs = {
            "cache_dir": cache_dir,
            "force_download": force_download,
            "proxies": proxies,
            "resume_download": resume_download,
            "local_files_only": local_files_only,
            "use_auth_token": use_auth_token,
            "revision": revision,
            "subfolder": subfolder,
            "_raise_exceptions_for_missing_entries": False,
            "_commit_hash": commit_hash,
        }
        if use_qigen and not QIGEN_AVAILABLE:
            logger.warning("Qigen is not installed, reset use_qigen to False.")
            use_qigen = False
        if use_triton and not TRITON_AVAILABLE:
            logger.warning("Triton is not installed, reset use_triton to False.")
            use_triton = False
        if not disable_exllama and not EXLLAMA_KERNELS_AVAILABLE:
            logger.warning(
                "Exllama kernel is not installed, reset disable_exllama to True. "
                "This may because you installed auto_gptq using a pre-build wheel "
                "on Windows, in which exllama_kernels are not compiled. To use "
                "exllama_kernels to further speedup inference, you can re-install "
                "auto_gptq from source."
            )
            disable_exllama = True
        if not disable_exllamav2 and not EXLLAMAV2_KERNELS_AVAILABLE:
            logger.warning(
                "Exllamav2 kernel is not installed, reset disable_exllamav2 to True. "
                "This may because you installed auto_gptq using a pre-build wheel "
                "on Windows, in which exllama_kernels are not compiled. To use "
                "exllama_kernels to further speedup inference, you can re-install "
                "auto_gptq from source."
            )
            disable_exllamav2 = True
        if not AUTOGPTQ_CUDA_AVAILABLE:
            logger.warning(
                "CUDA kernels for auto_gptq are not installed, this will result in "
                "very slow inference speed. This may because:\n"
                "1. You disabled CUDA extensions compilation by setting BUILD_CUDA_EXT=0 when install auto_gptq from source.\n"
                "2. You are using pytorch without CUDA support.\n"
                "3. CUDA and nvcc are not installed in your device."
            )

        if use_qigen and QIGEN_AVAILABLE:
            logger.warning("QIgen is active. Ignores all settings related to cuda.")
            inject_fused_attention = False
            inject_fused_mlp = False
            use_triton = False
            disable_exllama = True
            disable_exllamav2 = True

        if not disable_exllamav2 and not disable_exllama:
            logger.warning(
                "You have activated both exllama and exllamav2 kernel. Setting disable_exllama to True and keeping disable_exllamav2 to False"
            )
            disable_exllama = True

        # == step1: prepare configs and file names == #
        config = LlamaConfig.from_pretrained(
            model_name_or_path,
            trust_remote_code=trust_remote_code,
            **cached_file_kwargs,
        )

        if config.model_type not in SUPPORTED_MODELS:
            raise TypeError(f"{config.model_type} isn't supported yet.")

        if quantize_config is None:
            quantize_config = BaseQuantizeConfig.from_pretrained(model_name_or_path, **cached_file_kwargs, **kwargs)

        if not use_marlin and MARLIN_AVAILABLE:
            unsupported_reason = _validate_marlin_compatibility(quantize_config)
            if unsupported_reason is None and _validate_marlin_device_support():
                logger.info(
                    "You passed a model that is compatible with the Marlin int4*fp16 GPTQ kernel but use_marlin is False. We recommend using `use_marlin=True` to use the optimized Marlin kernels for inference. Example: `model = AutoGPTQForCausalLM.from_quantized(..., use_marlin=True)`."
                )

        if hasattr(quantize_config, "is_marlin_format") and quantize_config.is_marlin_format and not use_marlin:
            raise ValueError(
                "You passed a GPTQ model saved in int4*fp16 GPTQ Marlin kernel format but are loading with use_marlin=False. "
                "Please use `use_marlin=True` to load this model. Example: `model = AutoGPTQForCausalLM.from_quantized(..., use_marlin=True)`."
            )

        if model_basename is None:
            if quantize_config.model_file_base_name:
                possible_model_basenames = [quantize_config.model_file_base_name]
            else:
                possible_model_basenames = [
                    f"gptq_model-{quantize_config.bits}bit-{quantize_config.group_size}g",
                    "model",
                ]
        else:
            possible_model_basenames = [model_basename]

        quantize_config.model_name_or_path = model_name_or_path

        extensions = []
        if use_safetensors:
            extensions.append(".safetensors")
        else:
            extensions += [".bin", ".pt"]

        model_name_or_path = str(model_name_or_path)
        is_local = isdir(model_name_or_path)

        resolved_archive_file = None
        true_model_basename = None
        searched_files = []
        if is_local:
            for ext in extensions:
                for possible_model_basename in possible_model_basenames:
                    model_save_name = join(model_name_or_path, possible_model_basename)
                    searched_files.append(possible_model_basename + ext)
                    if isfile(model_save_name + ext):
                        resolved_archive_file = model_save_name + ext
                        true_model_basename = possible_model_basename
                        break
        else:  # remote
            temp = None
            for ext in extensions:
                for possible_model_basename in possible_model_basenames:
                    resolved_archive_file = cached_file(
                        model_name_or_path,
                        possible_model_basename + ext,
                        **cached_file_kwargs,
                    )
                    if resolved_archive_file is None:
                        resolved_archive_file = temp
                    searched_files.append(possible_model_basename + ext)
                    if resolved_archive_file is not None:
                        temp = resolved_archive_file
                        true_model_basename = possible_model_basename
                        break

        quantize_config.model_file_base_name = true_model_basename
        if resolved_archive_file is None:
            raise FileNotFoundError(
                f"Could not find a model in {model_name_or_path} with a name in {', '.join(searched_files)}. Please specify the argument model_basename to use a custom file name."
            )

        model_save_name = resolved_archive_file

        if (not disable_exllama or not disable_exllamav2) and trainable:
            logger.warning(
                "QuantLinear with the exllama backend not does support the trainable mode yet, switching to cuda/cuda_old/triton backend."
            )
            disable_exllama = True
            disable_exllamav2 = True

        elif not use_triton and trainable:
            logger.warning(
                "QuantLinear with cuda backend not support trainable mode yet, Switch to the pytorch backend."
            )

        # == step2: convert model to gptq-model (replace Linear with QuantLinear) == #
        def skip(*args, **kwargs):
            pass

        if torch_dtype is None:
            if not use_qigen:
                torch_dtype = torch.float16
            else:
                torch_dtype = torch.float32

        if torch_dtype != torch.float16:
            logger.warning("Overriding use_cuda_fp16 to False since torch_dtype is not torch.float16.")
            use_cuda_fp16 = False

        if not use_qigen:
            torch.nn.init.kaiming_uniform_ = skip
            torch.nn.init.uniform_ = skip
            torch.nn.init.normal_ = skip

            transformers.modeling_utils._init_weights = False

            init_contexts = [no_init_weights()]
            if low_cpu_mem_usage:
                init_contexts.append(accelerate.init_empty_weights(include_buffers=False))

            with ContextManagers(init_contexts):
                model = LlamaForCausalLM.from_pretrained(
                    model_name_or_path, config=config, trust_remote_code=trust_remote_code, torch_dtype=torch_dtype, device_map=device_map
                )

                layers = find_layers(model)
                ignore_layers = [cls.lm_head_name] + cls.outside_layer_modules
                for name in list(layers.keys()):
                    if any(name.startswith(ignore_layer) for ignore_layer in ignore_layers) or all(
                        not name.endswith(ignore_layer)
                        for sublist in cls.inside_layer_modules
                        for ignore_layer in sublist
                    ):
                        logger.info(f"The layer {name} is not quantized.")
                        del layers[name]

                make_quant(
                    model,
                    layers,
                    quantize_config.bits,
                    quantize_config.group_size,
                    use_triton=use_triton,
                    disable_exllama=disable_exllama,
                    disable_exllamav2=disable_exllamav2,
                    use_cuda_fp16=use_cuda_fp16,
                    desc_act=quantize_config.desc_act,
                    trainable=trainable,
                )
                model.tie_weights()

            # == step3: load checkpoint and dispatch == #
            if isinstance(device_map, str) and device_map not in [
                "auto",
                "balanced",
                "balanced_low_0",
                "sequential",
            ]:
                raise ValueError(
                    "If passing a string for `device_map`, please choose 'auto', 'balanced', 'balanced_low_0' or "
                    "'sequential'."
                )
            if isinstance(device_map, dict):
                max_memory = None
            else:
                if device is None and not device_map and not max_memory:
                    device_map = "auto"
                if device is not None:
                    device = torch.device(device)
                    if not max_memory and not device_map:
                        device_map = {"": device.index if device.type == "cuda" else device.type}
                if not isinstance(device_map, dict) and device_map != "sequential":
                    max_memory = accelerate.utils.get_balanced_memory(
                        model=model,
                        max_memory=max_memory,
                        no_split_module_classes=[cls.layer_type],
                        low_zero=(device_map == "balanced_low_0"),
                    )
            if not isinstance(device_map, dict):
                device_map = accelerate.infer_auto_device_map(
                    model,
                    max_memory=max_memory,
                    no_split_module_classes=[cls.layer_type],
                )

            if low_cpu_mem_usage:
                make_sure_no_tensor_in_meta_device(
                    model,
                    use_triton,
                    quantize_config.desc_act,
                    quantize_config.group_size,
                    bits=quantize_config.bits,
                    disable_exllama=disable_exllama,
                    disable_exllamav2=disable_exllamav2,
                )

            # TODO: move this logic in an awq_utils.py file.
            if quantize_config.awq_gemm_checkpoint:
                if use_marlin:
                    raise ValueError(
                        "Tried to load an AWQ kernel with use_marlin=True. This is currently not supported. Please open an issue in AutoGPTQ repository."
                    )

                if is_local:
                    is_cached = os.path.isfile(os.path.join(model_name_or_path, "autogptq_model.safetensors"))
                else:
                    namespace, subfolder = model_name_or_path.split("/")
                    assets_path = huggingface_hub.cached_assets_path(
                        library_name="autogptq",
                        namespace=namespace,
                        subfolder=subfolder,
                    )
                    weight_path = os.path.join(assets_path, "autogptq_model.safetensors")

                    is_cached = os.path.isfile(weight_path)

                if is_cached:
                    if is_local:
                        model_save_name = os.path.join(model_name_or_path, "autogptq_model.safetensors")
                    else:
                        namespace, subfolder = model_name_or_path.split("/")
                        assets_path = huggingface_hub.cached_assets_path(
                            library_name="autogptq",
                            namespace=namespace,
                            subfolder=subfolder,
                        )
                        model_save_name = os.path.join(assets_path, "autogptq_model.safetensors")

                    logger.info(f"Loading an AWQ model, detected a cached repacked weight at {model_save_name}.")
                else:
                    logger.info(
                        "Loading an AWQ model. This requires repacking the weights, and no repacking cached weight was found. Grab a coffee!"
                    )

                    if "safetensors" not in model_save_name:
                        raise NotImplementedError(
                            f"Conversion from AWQ checkpoints is implemented only for safetensors checkpoints, found {model_save_name}"
                        )
                    if quantize_config.bits != 4:
                        raise NotImplementedError(
                            f"Conversion from AWQ checkpoints is supported only for 4 bits models. Found {quantize_config.bits} bits."
                        )
                    gptq_layers = set()
                    non_gptq_params = set()
                    with safe_open(model_save_name, framework="pt") as f:
                        for state_dict_key in f.keys():
                            if (
                                "qweight" not in state_dict_key
                                and "qzeros" not in state_dict_key
                                and "scales" not in state_dict_key
                            ):
                                non_gptq_params.add(state_dict_key)
                                continue

                            # e.g. prefix "model.layers.3.self_attn.k_proj"
                            prefix, _ = state_dict_key.rsplit(".", 1)
                            gptq_layers.add(prefix)

                        new_state_dict = {}

                        for state_dict_key in non_gptq_params:
                            new_state_dict[state_dict_key] = f.get_tensor(state_dict_key)

                        gptq_layers = sorted(gptq_layers)
                        max_layer_name_length = len(max(gptq_layers, key=len))
                        pbar = tqdm(gptq_layers)
                        i = 0
                        for gptq_layer_name in pbar:
                            i += 1
                            desc = f"Unpacking {gptq_layer_name} + '...'"
                            desc = desc + " " * (max_layer_name_length - len(desc))

                            awq_qweight = f.get_tensor(gptq_layer_name + ".qweight")
                            awq_qzeros = f.get_tensor(gptq_layer_name + ".qzeros")
                            awq_scales = f.get_tensor(gptq_layer_name + ".scales")

                            # TODO: add FAST unpacking.
                            unpacked_qweight, unpacked_qzeros = unpack_awq(
                                awq_qweight,
                                awq_qzeros,
                                awq_scales,
                                bits=quantize_config.bits,
                                group_size=quantize_config.group_size,
                            )

                            # TODO: add FAST repacking, this is too slow.
                            desc = f"Repacking {gptq_layer_name}..."
                            desc = desc + " " * (max_layer_name_length + 12 - len(desc))
                            pbar.set_description(desc)
                            gptq_qweight, gptq_qzeros = pack_from_tensors(
                                unpacked_qweight,
                                unpacked_qzeros,
                                awq_scales,
                                bits=quantize_config.bits,
                                group_size=quantize_config.group_size,
                            )

                            new_state_dict[gptq_layer_name + ".qweight"] = gptq_qweight
                            new_state_dict[gptq_layer_name + ".qzeros"] = gptq_qzeros
                            new_state_dict[gptq_layer_name + ".scales"] = awq_scales

                    # Cache the converted model.
                    if is_local:
                        model_save_name = os.path.join(model_name_or_path, "autogptq_model.safetensors")
                        safe_save(new_state_dict, model_save_name)
                    else:
                        namespace, subfolder = model_name_or_path.split("/")
                        assets_path = huggingface_hub.cached_assets_path(
                            library_name="autogptq",
                            namespace=namespace,
                            subfolder=subfolder,
                        )
                        model_save_name = os.path.join(assets_path, "autogptq_model.safetensors")

                        safe_save(new_state_dict, model_save_name)

            if use_marlin:
                if torch.version.hip:
                    raise ValueError("Can not use Marlin int4*fp16 kernel with AMD ROCm version of PyTorch as the kernel is not compatible. Please do not use `use_marlin=True` when using ROCm devices.")
                if not torch.cuda.get_device_capability()[0] >= 8:
                    raise ValueError(f'Can not use Marlin int4*fp16 kernel with a device of compute capability {torch.cuda.get_device_capability()}, the minimum compute capability is 8.0 for Marlin kernel. Please do not use `use_marlin=True`, or please upgrade your GPU ("The more you buy, the more you save." - Taiwanese proverb).')

                # Validate the model can run in Marlin.
                if torch_dtype != torch.float16:
                    raise ValueError("Marlin kernel requires torch_dtype=torch.float16.")
                unsupported_reason = _validate_marlin_compatibility(quantize_config)
                if unsupported_reason is not None:
                    raise ValueError(
                        f"The model {model_name_or_path} can not be converted to use the Marlin kernel for the following reason: {unsupported_reason}, which is not supported by Marlin kernel."
                    )

                # Load the quant linear type we need.
                # TODO: load directy marlin with the right quantlinear class.
                quant_linear_class = dynamically_import_QuantLinear(
                    use_triton=use_triton,
                    desc_act=quantize_config.desc_act,
                    group_size=quantize_config.group_size,
                    bits=quantize_config.bits,
                    disable_exllama=disable_exllama,
                    disable_exllamav2=disable_exllamav2,
                    disable_marlin=True,  # Get the "original" QuantLienar class
                )

                # Prepare model for marlin load.
                #   If stub is marlin serialzed         --> load from directly
                #   If stub has cached marlin version   --> load from the cached versin
                #   Otherwise                           --> convert to marlin, cache, load from cache
                model, model_save_name = prepare_model_for_marlin_load(
                    model_name_or_path=model_name_or_path,
                    model=model,
                    quantize_config=quantize_config,
                    quant_linear_class=quant_linear_class,
                    torch_dtype=torch_dtype,
                    current_model_save_name=model_save_name,
                    device_map=device_map,
                )

                # Disable incompatible optimizations.
                if inject_fused_attention or inject_fused_mlp:
                    # TODO: Validate whether that can be used.
                    logger.info("Disabling fused attention and mlp injection because Marlin kernel is used.")
                    inject_fused_attention = False
                    inject_fused_mlp = False

            accelerate.utils.modeling.load_checkpoint_in_model(
                model,
                dtype=torch_dtype,  # This is very hacky but works due to https://github.com/huggingface/accelerate/blob/bd72a5f1a80d5146554458823f8aeda0a9db5297/src/accelerate/utils/modeling.py#L292
                checkpoint=model_save_name,
                device_map=device_map,
                offload_state_dict=True,
                offload_buffers=True,
            )

            # TODO: Why are we using this custom function and not dispatch_model?
            model = simple_dispatch_model(model, device_map)
        else:
            # Using QiGen.

            if quantize_config.desc_act:
                NotImplementedError("desc_act=True is not yet supported.")
            model = LlamaForCausalLM.from_config(
                config, trust_remote_code=trust_remote_code, torch_dtype=torch_dtype
            )

            layers = find_layers(model)
            ignore_layers = [cls.lm_head_name] + cls.outside_layer_modules
            for name in list(layers.keys()):
                if any(name.startswith(ignore_layer) for ignore_layer in ignore_layers):
                    logger.info(f"{name} not been quantized, will be ignored when make_quant.")
                    del layers[name]

            if model_save_name.endswith(".safetensors"):
                checkpoint = safe_load(model_save_name)
            else:
                checkpoint = torch.load(model_save_name)
            make_quant(
                model,
                layers,
                quantize_config.bits,
                quantize_config.group_size,
                use_triton=use_triton,
                disable_exllama=disable_exllama,
                disable_exllamav2=disable_exllamav2,
                use_cuda_fp16=use_cuda_fp16,
                desc_act=quantize_config.desc_act,
                trainable=trainable,
                use_qigen=True,
            )
            preprocess_checkpoint_qigen(
                model,
                layers,
                quantize_config.bits,
                quantize_config.group_size,
                checkpoint,
            )
            model.load_state_dict(checkpoint)

        # == step4: set seqlen == #
        model_config = model.config.to_dict()
        seq_len_keys = ["max_position_embeddings", "seq_length", "n_positions"]
        if any(k in model_config for k in seq_len_keys):
            for key in seq_len_keys:
                if key in model_config:
                    model.seqlen = model_config[key]
                    break
        else:
            logger.warning("can't get model's sequence length from model config, will set to 4096.")
            model.seqlen = 4096

        # == step5: (optional) inject optimized module == #
        if inject_fused_attention:
            if cls.fused_attn_module_type is None:
                inject_fused_attention = False
                logger.warning(f"{cls.__name__} hasn't fused attention module yet, will skip inject fused attention.")
            else:
                cls.fused_attn_module_type.inject_to_model(
                    model,
                    use_triton=use_triton,
                    group_size=quantize_config.group_size,
                    use_cuda_fp16=use_cuda_fp16,
                    desc_act=quantize_config.desc_act,
                    trainable=trainable,
                    bits=quantize_config.bits,
                    disable_exllama=disable_exllama,
                    disable_exllamav2=disable_exllamav2,
                )
        if inject_fused_mlp:
            if cls.fused_mlp_module_type is None:
                inject_fused_mlp = False
                logger.warning(f"{cls.__name__} hasn't fused mlp module yet, will skip inject fused mlp.")
            else:
                cls.fused_mlp_module_type.inject_to_model(model, use_triton=use_triton)

        # Any post-initialization that require device information, for example buffers initialization on device.
        model = autogptq_post_init(model, use_act_order=quantize_config.desc_act)

        model.eval()

        # == step6: (optional) warmup triton == #
        if use_triton and warmup_triton:
            from auto_gptq.nn_modules.qlinear.qlinear_triton import QuantLinear

            QuantLinear.warmup(model, seqlen=model.seqlen)

            if inject_fused_mlp and cls.fused_mlp_module_type is not None:
                cls.fused_mlp_module_type.warmup(model, seqlen=model.seqlen)

        # == step7: make model compatible with peft
        # cls.make_sure_compatible_with_peft(
        #     model,
        #     use_triton,
        #     quantize_config.desc_act,
        #     quantize_config.group_size,
        #     bits=quantize_config.bits,
        #     disable_exllama=disable_exllama,
        #     disable_exllamav2=disable_exllamav2,
        #     use_marlin=use_marlin,
        #     use_qigen=use_qigen,
        # )

        return cls(
            model,
            True,
            quantize_config,
            is_triton_backend=use_triton,
            injected_fused_attention=inject_fused_attention,
            injected_fused_mlp=inject_fused_mlp and use_triton,
            trainable=trainable,
        )

    def __getattr__(self, item):
        try:
            return super().__getattr__(item)
        except Exception:
            return getattr(self.model, item)


__all__ = ["BaseGPTQForCausalLM", "BaseQuantizeConfig"]

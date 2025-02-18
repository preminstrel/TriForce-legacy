CUDA_VISIBLE_DEVICES=4 nohup python test/streamllm.py --dataset pg19 --prefill 2048 --budget 0.1 --log_csv > archive/log/evict_0.log &
CUDA_VISIBLE_DEVICES=5 nohup python test/streamllm.py --dataset pg19 --prefill 4096 --budget 0.1 --log_csv > archive/log/evict_0.log &
CUDA_VISIBLE_DEVICES=6 nohup python test/streamllm.py --dataset pg19 --prefill 8192 --budget 0.1 --log_csv > archive/log/evict_0.log &

CUDA_VISIBLE_DEVICES=7 nohup python test/streamllm.py --dataset pg19 --prefill 49152 --budget 0.1 --log_csv > archive/log/evict_0.log &
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 4096 --budget 0.1 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 4096 --budget 0.08 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 4096 --budget 0.08 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 4096 --budget 0.05 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 4096 --budget 0.05 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 4096 --budget 0.03 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 4096 --budget 0.03 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 4096 --budget 0.02 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 4096 --budget 0.02 --log_csv

# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 4096 --budget 0.1 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 4096 --budget 0.1 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 4096 --budget 0.08 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 4096 --budget 0.08 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 4096 --budget 0.05 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 4096 --budget 0.05 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 4096 --budget 0.03 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 4096 --budget 0.03 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 4096 --budget 0.02 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 4096 --budget 0.02 --log_csv --target llama-7B-1M

# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 8192 --budget 0.1 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 8192 --budget 0.1 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 8192 --budget 0.08 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 8192 --budget 0.08 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 8192 --budget 0.05 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 8192 --budget 0.05 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 8192 --budget 0.03 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 8192 --budget 0.03 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 8192 --budget 0.02 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 8192 --budget 0.02 --log_csv

# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 8192 --budget 0.1 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 8192 --budget 0.1 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 8192 --budget 0.08 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 8192 --budget 0.08 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 8192 --budget 0.05 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 8192 --budget 0.05 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 8192 --budget 0.03 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 8192 --budget 0.03 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 8192 --budget 0.02 --log_csv --target llama-7B-1M
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 8192 --budget 0.02 --log_csv --target llama-7B-1M

CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 16384 --budget 0.1 --log_csv
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 16384 --budget 0.1 --log_csv
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 16384 --budget 0.08 --log_csv
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 16384 --budget 0.08 --log_csv
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 16384 --budget 0.05 --log_csv
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 16384 --budget 0.05 --log_csv
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 16384 --budget 0.03 --log_csv
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 16384 --budget 0.03 --log_csv
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 16384 --budget 0.02 --log_csv
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 16384 --budget 0.02 --log_csv

CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 16384 --budget 0.1 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 16384 --budget 0.1 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 16384 --budget 0.08 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 16384 --budget 0.08 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 16384 --budget 0.05 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 16384 --budget 0.05 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 16384 --budget 0.03 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 16384 --budget 0.03 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 16384 --budget 0.02 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 16384 --budget 0.02 --log_csv --target llama-7B-1M

# CUDA_VISIBLE_DEVICES=9 python test/streamllm.py --dataset pg19 --prefill 16384 --budget 0.02 --target llama-7B-1M --verbose
# CUDA_VISIBLE_DEVICES=9 python test/streamllm.py --dataset password --prefill 16384 --budget 0.02 --log_csv --target llama-7B-1M

# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 32768 --budget 0.1 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 32768 --budget 0.1 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 32768 --budget 0.08 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 32768 --budget 0.08 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 32768 --budget 0.05 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 32768 --budget 0.05 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 32768 --budget 0.03 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 32768 --budget 0.03 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 32768 --budget 0.02 --log_csv
# CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 32768 --budget 0.02 --log_csv

CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 32768 --budget 0.1 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 32768 --budget 0.1 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 32768 --budget 0.08 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 32768 --budget 0.08 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 32768 --budget 0.05 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 32768 --budget 0.05 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 32768 --budget 0.03 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 32768 --budget 0.03 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset pg19 --prefill 32768 --budget 0.02 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1 python test/streamllm.py --dataset password --prefill 32768 --budget 0.02 --log_csv --target llama-7B-1M

CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 65536 --budget 0.1 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 65536 --budget 0.1 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 65536 --budget 0.08 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 65536 --budget 0.08 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 65536 --budget 0.05 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 65536 --budget 0.05 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 65536 --budget 0.03 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 65536 --budget 0.03 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 65536 --budget 0.02 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 65536 --budget 0.02 --log_csv

CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 65536 --budget 0.1 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 65536 --budget 0.1 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 65536 --budget 0.08 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 65536 --budget 0.08 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 65536 --budget 0.05 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 65536 --budget 0.05 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 65536 --budget 0.03 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 65536 --budget 0.03 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 65536 --budget 0.02 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 65536 --budget 0.02 --log_csv --target llama-7B-1M

CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 130072 --budget 0.1 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 130072 --budget 0.1 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 130072 --budget 0.08 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 130072 --budget 0.08 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 130072 --budget 0.05 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 130072 --budget 0.05 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 130072 --budget 0.03 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 130072 --budget 0.03 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 130072 --budget 0.02 --log_csv
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 130072 --budget 0.02 --log_csv

CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 130072 --budget 0.1 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 130072 --budget 0.1 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 130072 --budget 0.08 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 130072 --budget 0.08 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 130072 --budget 0.05 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 130072 --budget 0.05 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 130072 --budget 0.03 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 130072 --budget 0.03 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset pg19 --prefill 130072 --budget 0.02 --log_csv --target llama-7B-1M
CUDA_VISIBLE_DEVICES=1,2 python test/streamllm.py --dataset password --prefill 130072 --budget 0.02 --log_csv --target llama-7B-1M
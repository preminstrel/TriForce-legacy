CUDA_VISIBLE_DEVICES=4 python test/graph_chain.py  --gamma 5 --prefill 256 --budget 0.1 --dataset pg19 --log_csv
CUDA_VISIBLE_DEVICES=4 python test/graph_chain.py  --gamma 5 --prefill 512 --budget 0.1 --dataset pg19 --log_csv
CUDA_VISIBLE_DEVICES=5 python test/graph_chain.py  --gamma 5 --prefill 1024 --budget 0.1 --dataset pg19 --log_csv
CUDA_VISIBLE_DEVICES=3 python test/graph_chain.py  --gamma 5 --prefill 2048 --budget 0.1 --dataset pg19 --log_csv
CUDA_VISIBLE_DEVICES=1 python test/graph_chain.py  --gamma 5 --prefill 4096 --budget 0.1 --dataset pg19 --log_csv
CUDA_VISIBLE_DEVICES=6 python test/graph_chain.py  --gamma 5 --prefill 8192 --budget 0.1 --dataset pg19 --log_csv
CUDA_VISIBLE_DEVICES=9 python test/graph_chain.py  --gamma 5 --prefill 16384 --budget 0.1 --dataset pg19 --log_csv
CUDA_VISIBLE_DEVICES=3 python test/graph_chain.py  --gamma 5 --prefill 32768 --budget 0.1 --dataset pg19 --log_csv
CUDA_VISIBLE_DEVICES=3 python test/graph_chain.py  --gamma 5 --prefill 49152 --budget 0.1 --dataset pg19 --log_csv
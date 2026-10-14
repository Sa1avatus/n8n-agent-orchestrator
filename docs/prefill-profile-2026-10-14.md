# Profile of cold prefill, 2026-10-14

Measured on the prod model child (same image, arguments, mounts and environment as the router starts it, `llama-prof` container, port 8040), RTX 3060 12 GB,
`cache_prompt: false`, one request, n_predict 1. Script: `data-tables/ops/profile_prefill.sh`. Raw files: `data-tables/ops/prefill_profile/{p110,p170,nsys}`
(`timing.txt`, `gpu_during.csv`, `stats.txt`, `prefill.nsys-rep`, `server.log`).

## Speed, power limit 110.5 W against 170 W (no profiler)

| prompt | 110.5 W | 170 W | gain |
|---|---|---|---|
| 12 558 tokens | 343.6 tok/s | 426.2 tok/s | +24 % |
| 51 000 tokens | 282.9 tok/s | 356.7 tok/s | +26 % |

At 110.5 W the card sits on the power limit (average 110 W, SM clock about 1555 MHz, memory 8301 MHz, 65 C); at 170 W: SM about 1755 MHz, 169 W, 82 C.
Under Nsight Systems at 110.5 W: 12 558 tokens 368 tok/s, 31 087 tokens 336 tok/s (the profiler adds a few percent).

## Nsight Systems, 31 087-token prompt, ubatch 128, 110.5 W (the last request only)

Wall time of the request 92.5 s. Sum of GPU kernel time about 66 s, so the GPU stands idle about 28 % of the wall time.

| share of GPU kernel time | kernel | calls | avg |
|---|---|---|---|
| 17.9 % | `flash_attn_ext_f16<256,256,8,8>` (attention, 16 full-attention layers + draft) | 2074 | 5.73 ms |
| 12.9 % | `mul_mat_q<ggml_type 18 = IQ3_XXS, mmq_x 128>` | 8742 | 0.98 ms |
| 12.2 % | `mul_mat_q<type 22 = IQ2_S>` | 5607 | 1.45 ms |
| 10.3 % | `mul_mat_q<type 10 = Q2_K>` | 4017 | 1.71 ms |
| 9.8 % | `gated_delta_net_cuda<128>` (48 GDN layers) | 3780 | 1.73 ms |
| 9.2 % | `mul_mat_q<type 21 = IQ3_S>` | 6800 | 0.90 ms |
| 6.6 % | `mul_mat_q<type 17 = IQ2_XS>` | 3319 | 1.31 ms |
| 5.7 % | `mul_mat_q<type 16 = IQ2_XXS>` | 3472 | 1.09 ms |
| 1.6 % | `unary_gated_op_kernel<silu>` | 9769 | 0.11 ms |
| 1.4 % | `mul_mat_q<type 14 = Q6_K>` | 968 | 0.99 ms |
| 1.3 % | `quantize_mmq_q8_1` | 30 696 | 0.03 ms |
| 1.0 % | `dequantize_block_q4_0<half>` (q4_0 KV to f16 before the attention) | 3936 | 0.17 ms |
| rest | norms, bin_bcast, concat, cublas `ampere_h1688gemm` (0.8 %) ... | | |

All `mul_mat_q` together about 60 % of kernel time: the quantized matrix products are the ceiling. Attention about 19 % (it grows with the context: the speed falls
from about 380 to about 265 tok/s at 94K). The GDN kernel is about 10 %, NOT the main cost.

## Memory traffic and the CPU side

- Host-to-device copies: 143.8 GB in 7996 copies (avg 18 MB) for 31 087 tokens = 243 ubatches of 128, about 590 MB per ubatch (the FFN gate/up weights of the 24
  blocks kept in host memory, streamed with GGML_CUDA_WEIGHT_PREFETCH). Copy time 5.6 s in total, so at about 25 GB/s it is hidden behind compute, but the same
  weights are copied again for EVERY ubatch: a larger ubatch halves the bytes per prompt token (measured earlier: ubatch 128 -> 256 gave +12 %; 512 does not fit in VRAM).
- 224 288 `cudaLaunchKernel` calls (about 920 kernels per ubatch), 19.9 s of CPU time in launches (inflated by the profiler); only 4980 graph launches. The
  prefill runs mostly without CUDA graphs. `cudaStreamSynchronize` is 74 % of the API time.
- Small kernels (rms_norm, bin_bcast, silu, concat, quantize_mmq_q8_1) are about 8 % of kernel time but number tens of thousands of launches.

## Conclusions for a patch (each one to be proved by measurements)

1. Per-ubatch fixed cost (launches, synchronisation, weight streaming): bigger ubatch (needs VRAM), fewer and fused kernels, CUDA graphs for the prefill ubatch.
2. `mul_mat_q` for the i-quant types on sm_86: check by code whether tensor cores (mma) or dp4a are used and which tile (mmq_x) is chosen for batch 128/256.
3. Attention: `flash_attn_ext_f16` with head size 256 on sm_86, and the q4_0 -> f16 copy before it.
4. GDN prefill kernel: about 10 % at best, lowest priority.
5. Power: +25 % from 170 W instead of 110.5 W (governor `data-tables/ops/gpu_power_governor.py`, dry-run); independent of the above.

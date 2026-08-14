"""T5 latency ladder: vLLM serving configs and the measurement harness.

Config-level optimization only (quantization, prefix caching, speculative decoding).
Accuracy is re-measured at every rung; no custom kernels, no fine-tuning.
"""

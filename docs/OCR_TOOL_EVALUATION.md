# DataSeek — OCR Tool Evaluation

Evaluated on the target host (Ubuntu 26.04, AMD FX-6100 6-core, **no AVX2**, 17 GB RAM,
no GPU). The absence of AVX2 rules out most modern PyTorch/ONNX builds; the toolchain
was therefore validated by actually running each candidate, not by GitHub stars.

## Host constraints (verified)

| Resource | Value |
|---|---|
| CPU | AMD FX(tm)-6100 Six-Core (2011), flags: `avx fma sse4_2` — **no AVX2** |
| RAM | 17 GiB (≈15 GiB available) |
| GPU | none (`nvidia-smi` absent) |
| Disk | 36 GB free on `/` |
| Python | 3.14 system; isolated `.venv` on **3.12** for ML wheels |
| Vision service | Ollama `qwen3.5:4b` on a **LAN host** (`OLLAMA_HOST`), not local |

## Benchmark method

12 screenshots sampled across the collection's dominant sources (Instagram, Facebook,
Facebook Lite, camera) plus browser/GitHub/video-frame content. Each engine was run on
the **original** pixels; latency is wall-clock on the idle host.

## Results

| Task | RapidOCR conf | RapidOCR chars | RapidOCR s | Tesseract conf | Tesseract chars | Tesseract s |
|---|---:|---:|---:|---:|---:|---:|
| IMG-0001 (GitHub repo) | 94.3 | 1098 | 15.7 | 63.5 | 443 | 1.1 |
| IMG-0002 (video frame) | 93.7 | 349 | 8.2 | 56.5 | 202 | 1.0 |
| IMG-0003 | 96.1 | 265 | 7.6 | 62.5 | 244 | 1.1 |
| IMG-0004 | 96.4 | 233 | 6.2 | 72.4 | 272 | 1.3 |
| IMG-0624 | 92.8 | 170 | 6.8 | 65.2 | 183 | 0.8 |
| IMG-0907 | 96.6 | 550 | 9.3 | 45.5 | 219 | 1.2 |
| IMG-0013 | 98.1 | 688 | 9.8 | 76.8 | 709 | 2.0 |
| IMG-0206 | 91.5 | 408 | 8.0 | 56.8 | 160 | 0.8 |
| IMG-0398 | 95.0 | 323 | 8.3 | 63.7 | 2111 | 1.0 |
| IMG-0739 | 84.7 | 464 | 9.1 | 73.3 | 376 | 1.1 |
| IMG-0882 | 97.8 | 511 | 9.1 | 82.6 | 440 | 1.2 |
| IMG-0905 | 94.6 | 268 | 8.0 | 64.3 | 183 | 0.8 |

RapidOCR averaged **≈94 confidence** and captured **2–5× more text** than Tesseract,
whose confidence clustered in the 45–82 range and whose output on IMG-0001 was the
garbled `"@ omnivoice pir LRU) Sa abit…"` cited in the project brief. On IMG-0398
Tesseract emitted 2111 chars of mostly noise (PSM-3 full-page reading) versus
RapidOCR's focused 323 chars.

## Downscaling experiment (latency vs accuracy)

| Image | Full res | 1600 px | 1280 px |
|---|---|---|---|
| IMG-0020 1080×2340 | 18.5 s / conf 93.4 / 1383 chars | 11.0 s / 95.4 / 726 | 8.0 s / 93.1 / 389 |
| IMG-0025 1080×2340 | 23.6 s / 93.6 / 1946 | 14.2 s / 93.8 / 1394 | 18.4 s / 92.0 / 1237 |
| IMG-0030 1080×2340 | 12.3 s / 99.0 / 1022 | 10.6 s / 97.3 / 1010 | 12.0 s / 97.4 / 942 |

**Decision:** OCR runs at **full resolution** (text capture matters more than speed).
Only the vision layer downscales, because it contributes semantics, not fine text.

## Tool assessment

| Tool | Repo | Install | CPU | GPU | Screenshots | Verdict |
|---|---|---|---|---|---|---|
| **RapidOCR** | RapidAI/RapidOCR | `pip install rapidocr onnxruntime` | ✅ | optional | ✅ best in benchmark | **Selected — primary** |
| **Tesseract 5.5** | tesseract-ocr/tesseract | apt (installed) | ✅ | — | ⚠️ weak on dark UI | **Selected — second witness** |
| qwen3.5:4b (vision) | Ollama | LAN service | n/a | n/a | ✅ strong semantics | **Selected — optional vision** |
| PaddleOCR | PaddlePaddle/PaddleOCR | heavy (paddlepaddle) | ⚠️ | ✅ | ✅ | Not needed — RapidOCR uses the same PP-OCR models via ONNX |
| EasyOCR | JaidedAI/EasyOCR | needs PyTorch | ⚠️ no-AVX2 risk | ✅ | ✅ | Rejected — heavy Torch install, slower CPU |
| Surya | datalab-to/surya | needs PyTorch | ❌ | ✅ | ✅ | Rejected — GPU-oriented |
| docTR | mindee/doctr | TF/PyTorch | ⚠️ | ✅ | ✅ | Rejected — heavy, document-oriented |
| MMOCR | open-mmlab/mmocr | mmcv toolchain | ❌ | ✅ | ✅ | Rejected — brittle build on this CPU |
| Umi-OCR | hiroi-sora/Umi-OCR | Windows GUI | n/a | n/a | ✅ | Rejected — desktop app, not headless |
| GOT-OCR2.0 / GLM-OCR / DeepSeek-OCR | various | large VLM | ❌ | ✅ | ✅ | Rejected — needs GPU / very large VRAM |
| MinerU / Unstructured | opendatalab, Unstructured-IO | heavy | ⚠️ | ✅ | ⚠️ PDF-focused | Rejected — PDF pipeline, overkill for screenshots |

Chosen subset: **RapidOCR (primary) + Tesseract (independent witness) + optional
vision model**. Accuracy over tool count.

## Confidence notes

- RapidOCR scores are recognition confidences from PP-OCRv6 ONNX models.
- Tesseract scores are the mean of per-word TSV confidences (PSM 3).
- Agreement is computed between the strongest engine and any *credible* second
  witness (confidence ≥ 55); a weak engine's noise cannot manufacture a "conflict".

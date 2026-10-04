#!/usr/bin/env bash
# Install EasyOCR (JaidedAI/EasyOCR) into an isolated venv.
#
# A separate environment is used deliberately: the running OCR batch must not
# have its numpy/opencv/torch replaced underneath it. The pipeline calls this
# interpreter as a subprocess engine, one image at a time.
#
# CPU-only torch is requested via --torch-backend=cpu so we do not pull the
# multi-gigabyte CUDA wheels this machine cannot use (no NVIDIA GPU).
set -u
cd /home/chris/dataseek
export PATH="$HOME/.local/bin:$PATH"
VENV=/home/chris/dataseek/.venv-easyocr

echo "=== $(date -Is) creating venv ==="
uv venv "$VENV" --python 3.12 || exit 1

echo "=== $(date -Is) installing torch (cpu) ==="
VIRTUAL_ENV="$VENV" uv pip install --torch-backend=cpu torch torchvision || exit 1

echo "=== $(date -Is) installing easyocr ==="
VIRTUAL_ENV="$VENV" uv pip install --torch-backend=cpu easyocr || exit 1

echo "=== $(date -Is) verifying import ==="
"$VENV/bin/python" -c "import easyocr, torch; print('easyocr', easyocr.__version__ if hasattr(easyocr,'__version__') else 'ok'); print('torch', torch.__version__)" || exit 1
echo "=== $(date -Is) DONE ==="
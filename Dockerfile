FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies first (separate layer so this is cached
# and only re-runs when requirements.txt actually changes, not on
# every code edit).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download the spaCy English model at BUILD time, not at request time
# or even at server-startup time. This is what was previously missing
# on Render (it silently fell back to skipping spaCy's contribution to
# the semantic check).
RUN python -m spacy download en_core_web_sm

# Now copy the rest of the app
COPY . .

# Default port for local runs / platforms that don't inject their own.
# Cloud Run (and most other platforms) set PORT themselves at container
# start time - this ENV line just gives a sane fallback, it does NOT
# override whatever the platform actually passes in.
ENV PORT=7860
EXPOSE 7860

# Single worker: this app loads spaCy + torch/sentence-transformers into
# memory once at startup, and each gunicorn worker would load its own
# separate copy - one worker keeps memory usage predictable on small
# free-tier instances. --timeout 120 gives slow requests (page
# extraction + AI calls) enough room to finish instead of being killed
# mid-response.
#
# Uses shell form (not exec-form JSON array) specifically so $PORT is
# expanded at container startup - this is what lets the SAME image run
# on Cloud Run (which injects PORT=8080), Render, HF Spaces (7860), or
# anywhere else, without editing the Dockerfile per platform.
CMD gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1

# Container image for cloud deployment. Local users can just `pip install` the wheel instead.
FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

# No long-running server entrypoint yet (arrives with the Phase 2 driver loop); until then the
# default command is the pass-through smoke so `docker run <image>` self-checks the build.
CMD ["osf-smoke"]

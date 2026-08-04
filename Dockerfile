# rendercheck with ffmpeg already in it.
#
# The library has no Python dependencies, but it does need ffmpeg, and "install
# Python, then install ffmpeg, then install this" is three steps too many for
# someone whose build is a Node container. This is one step.
#
#   docker run --rm -v "$PWD:/work" ghcr.io/rogermsc/rendercheck check /work/out.mp4
#
# ffmpeg is not pinned on purpose: the checks read what its filters report, and
# a version older than the distribution's is not a configuration anyone should
# be encouraged into. If a filter's output format ever changes under us, that is
# a bug to fix here rather than to freeze around.

FROM python:3.13-slim

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copied and installed rather than pulled from PyPI, so the image built from a
# commit contains that commit. An image that silently ships a different version
# than the tag it was built from is its own silent failure.
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY rendercheck ./rendercheck
RUN pip install --no-cache-dir . && rm -rf /src

# Media gets mounted here. Left as the working directory so relative paths in
# the failure messages read the way the caller wrote them.
WORKDIR /work

ENTRYPOINT ["rendercheck"]
CMD ["demo"]

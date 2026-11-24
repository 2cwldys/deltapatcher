# An example to copy into your own image: its files come from your DeltaPatcher release when the container
# starts, and only what changed is downloaded. Replace the CMD with how your program starts.
#
# The DeltaPatcher command line needs glibc 2.35 or later (Debian 12, Ubuntu 22.04 and newer). On Alpine
# or anything older, install the Python package instead: see docker/README.md.
FROM debian:bookworm-slim

# a version (0.3.0) to pin one, or latest
ARG DELTAPATCHER_VERSION=latest
RUN set -e; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    if [ "$DELTAPATCHER_VERSION" = latest ]; then \
        url=https://github.com/2cwldys/deltapatcher/releases/latest/download/deltapatcher-cli-linux-x64.tar.gz; \
    else \
        url=https://github.com/2cwldys/deltapatcher/releases/download/v$DELTAPATCHER_VERSION/deltapatcher-cli-linux-x64.tar.gz; \
    fi; \
    curl -fsSL "$url" | tar -xz -C /usr/local/bin; \
    apt-get purge -y curl; apt-get autoremove -y; rm -rf /var/lib/apt/lists/*; \
    deltapatcher-cli --no-log version

COPY entrypoint.sh /usr/local/bin/update-then-start
RUN chmod +x /usr/local/bin/update-then-start

# the files live on a volume, so the next start downloads only what changed since
VOLUME /data
WORKDIR /data
ENTRYPOINT ["update-then-start"]
CMD ["./start-server.sh"]

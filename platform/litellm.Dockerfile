# syntax=docker/dockerfile:1

ARG LITELLM_BASE_IMAGE
FROM ${LITELLM_BASE_IMAGE}

COPY --from=certs . /tmp/certs/
RUN set -eu; \
    for cert in /tmp/certs/*.pem /tmp/certs/*.crt; do \
        [ -f "${cert}" ] || continue; \
        cat "${cert}" >> /etc/ssl/certs/ca-certificates.crt; \
    done; \
    rm -rf /tmp/certs

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

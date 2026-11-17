# syntax=docker/dockerfile:1
# Proxy HTTPS (Caddy + Let's Encrypt) endurecido para rodar sem root e sem capabilities.
#
# Por que compilar o Caddy em vez de usar o binário oficial:
#  1. Dependências corrigidas: o caddy 2.11.9 oficial foi compilado com golang.org/x/net v0.60.0
#     (CVE-2026-78661, HIGH, corrigida na 0.62.0). Compilamos com a x/net atualizada, e o Trivy
#     da pipeline bloqueia a imagem se algo HIGH/CRITICAL com correção reaparecer.
#  2. Sem file capability: o binário oficial vem marcado com cap_net_bind_service, e com
#     no-new-privileges + cap_drop ALL + usuário não-root (nosso compose) o kernel recusa
#     executá-lo ("exec: operation not permitted"). O binário compilado aqui não tem essa marca;
#     o Caddy escuta em 8080/8443 e o Docker publica 80/443.

# ---- build ----
FROM caddy:2.11.9-builder-alpine AS builder
# A imagem builder oficial define XCADDY_SETCAP=1, que faz o xcaddy gravar cap_net_bind_service
# no binário (e o COPY --from preserva esse atributo). Desligamos e, por garantia, removemos a marca.
ENV XCADDY_SETCAP=0
RUN xcaddy build v2.11.9 \
      --replace golang.org/x/net=golang.org/x/net@v0.63.0 \
      --output /usr/bin/caddy && \
    (setcap -r /usr/bin/caddy 2>/dev/null || true) && \
    if command -v getcap > /dev/null && [ -n "$(getcap /usr/bin/caddy)" ]; then \
      echo "caddy ainda tem file capability" >&2; exit 1; \
    fi

# ---- runtime ----
FROM caddy:2.11.9-alpine
USER root
RUN apk upgrade --no-cache && \
    mkdir -p /data /config && chown -R 10002:10002 /data /config
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile

USER 10002:10002
EXPOSE 8080 8443

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

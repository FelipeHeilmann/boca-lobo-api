# syntax=docker/dockerfile:1
# Proxy HTTPS (Caddy + Let's Encrypt) endurecido para rodar sem root e sem capabilities.
#
# A imagem oficial marca /usr/bin/caddy com a file capability cap_net_bind_service. Com
# no-new-privileges + cap_drop ALL + usuário não-root (nosso compose), o kernel recusa executar
# um binário assim ("exec: operation not permitted"). Como o Caddy escuta em 8080/8443 (o Docker
# publica 80/443), essa capability é desnecessária: removemos do binário.
FROM caddy:2.11.9-alpine

USER root
RUN apk upgrade --no-cache && \
    apk add --no-cache --virtual .setcap libcap && \
    setcap -r /usr/bin/caddy && \
    apk del .setcap && \
    mkdir -p /data /config && chown -R 10002:10002 /data /config

COPY Caddyfile /etc/caddy/Caddyfile

USER 10002:10002
EXPOSE 8080 8443

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

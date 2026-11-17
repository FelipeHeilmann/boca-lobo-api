#!/bin/bash
# Provisionamento inicial do EC2: Docker + Compose v2 + AWS CLI.
# Nenhum segredo aqui: o pull do ECR usa a IAM Role da instância.
set -euxo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y docker.io docker-compose-v2 unzip curl

# AWS CLI v2 (usado para o login no ECR durante o deploy)
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install --update
rm -rf /tmp/aws /tmp/awscliv2.zip

systemctl enable --now docker

# Atualizações automáticas de segurança do SO (responsabilidade do cliente no modelo compartilhado)
apt-get install -y unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

mkdir -p /opt/boca-de-lobo
chmod 750 /opt/boca-de-lobo

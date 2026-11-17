# 🌊 CitizenTech - Projeto Boca de Lobo (MVP)

> **Global Solution 2026.2 - Secure DevOps Tools & Cloud Computing**

## 🎯 Objetivo do Projeto
O **Boca de Lobo** é um MVP de engenharia de software focado em segurança e emergências urbanas (enchentes). Antes de cada temporal, o sistema cruza o status de obstrução de bueiros/sarjetas com previsões meteorológicas para disparar ações preventivas para a equipe de limpeza e alertas de rota segura para moradores de regiões de risco.

---

## 🛠️ Ficha Técnica
| Tema | Implementação |
|---|---|
| Aplicação | API FastAPI (Python 3.11) que serve ao mapa do cidadão o retrato da camada Ouro lido do DynamoDB, com métricas Prometheus (4 sinais de ouro) |
| Ingestão de dados | Databricks envia o export para o S3 → Lambda valida → DynamoDB (Always Free) |
| Container | Multi-stage, usuário não-root (uid 10001), sem segredos, `read_only`, `cap_drop: ALL` |
| Execução | Docker Compose no EC2 (`docker-compose.yml`): Caddy (HTTPS Let's Encrypt via sslip.io) + API |
| Nuvem (IaC) | Terraform na AWS: EC2 + SG + EIP + ECR + S3 + Lambda + DynamoDB + IAM; state remoto em S3 com lock |
| Acesso admin | **Nenhuma porta admin aberta** — administração via AWS SSM. SSH opcional só para `/32`; `0.0.0.0/0` é rejeitado pela validação |
| CI/CD | Monorepo com um pipeline: Gitleaks, Semgrep, testes (API + Lambda), Terraform validate, Trivy (config + imagem) a cada push; na `master`: `terraform apply` (infra + Lambda) + deploy da API via SSM |
| Autenticação na AWS | GitHub OIDC → IAM Role (sem access keys) |
| Observabilidade | Prometheus + Alertmanager + Grafana locais apontando para o MVP publicado |

Documentação complementar:
- [Arquitetura, responsabilidade compartilhada, LGPD e custo](docs/arquitetura.md)
- [Ingestão do mapa: Databricks → S3 → Lambda → DynamoDB](docs/ingestao-dados.md)
- [Pré-requisitos AWS: role de deploy, OIDC e bucket do state](docs/aws-role.md)
- [Evidências (pipeline barrada/corrigida, dashboard, alerta)](docs/evidencias.md)

### Estrutura
```
app/                     API FastAPI + Dockerfile + testes          → container no EC2
lambda/ingestor/         Lambda que valida o JSON e grava no Dynamo → publicada pelo Terraform
caddy/Caddyfile          Proxy HTTPS (Let's Encrypt)
docker-compose.yml       Stack executada no EC2 (proxy + api)
terraform/               main.tf (EC2/SG/EIP/ECR) + dados.tf (S3/Lambda/DynamoDB/IAM)
.github/workflows/       Pipeline CI/CD única do monorepo
monitoring/              Prometheus, Alertmanager, Grafana (local)
scripts/                 publicar_mapa, gen-targets, load, trigger-alert
docs/                    Arquitetura, ingestão, IAM e evidências
```

---

## 🚀 Passo a passo reproduzível

### 0. Pré-requisitos
- Conta AWS com o OIDC provider do GitHub (`token.actions.githubusercontent.com`) e um bucket S3 privado e versionado para o Terraform state
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) autenticado (`aws sts get-caller-identity`)
- [Terraform ≥ 1.10](https://developer.hashicorp.com/terraform/install) (apenas para `terraform output`/`destroy` locais)
- Docker + Docker Compose v2
- Fork/clone deste repositório no GitHub

### 1. Rodar localmente (opcional)
```bash
pip install -r app/requirements.txt pytest "httpx<0.29"
python -m pytest -q app/tests lambda/ingestor/tests     # testes da API e da Lambda

docker build -t boca-de-lobo-api:dev app
docker run --rm -p 8000:8000 -e DYNAMODB_TABLE=boca-lobo-dynamo-mapa -e AWS_REGION=us-east-1 \
  --env-file <(aws configure export-credentials --format env-no-export) \
  boca-de-lobo-api:dev                                    # credenciais temporárias do seu perfil
curl http://localhost:8000/health
```

### 2. Criar a role de deploy na AWS (uma vez)
A pipeline assume via OIDC a role dedicada `boca-lobo-github-deploy`. A trust policy aceita só `repo:FelipeHeilmann/boca-lobo-api:environment:production` e as permissões são mínimas. Os comandos estão em [docs/aws-role.md](docs/aws-role.md#como-criar-a-role-aws-cli) e os documentos IAM em [`docs/iam/`](docs/iam).

> Se usar um fork, troque `FelipeHeilmann/boca-lobo-api` em `docs/iam/github-deploy-trust.json`.

### 3. Configurar o GitHub
Em **Settings → Environments → New environment** crie `production` (em *Deployment branches* restrinja a `master`). Dentro dele cadastre:

| Nome | Tipo | Valor |
|---|---|---|
| `AWS_ROLE_ARN` | Secret | `arn:aws:iam::<CONTA>:role/boca-lobo-github-deploy` |
| `TF_STATE_BUCKET` | Secret | `nortex-tfstate` (ou o seu bucket de state) |
| `TF_VAR_admin_cidrs` | Secret *(opcional)* | `["SEU.IP.PUBLICO/32"]` para liberar SSH; omita para usar apenas SSM |
| `AWS_REGION` | Variable | `us-east-1` |
| `TF_VAR_project` | Variable *(opcional)* | `boca-de-lobo` |
| `CORS_ORIGINS` | Variable *(opcional)* | origem do frontend; padrão `https://felipevoidela.github.io` |

Não é necessário cadastrar `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`: a pipeline usa OIDC.

### 4. Deploy
```bash
git push origin master
```
A pipeline (`.github/workflows/ci-cd.yml`) executa:

| Job | Em todo push | Só na `master` |
|---|---|---|
| Gitleaks | ✅ | |
| Semgrep | ✅ | |
| Testes (pytest da API e da Lambda) | ✅ | |
| Terraform fmt/validate + Trivy config | ✅ | |
| Docker build + Trivy image | ✅ | |
| Terraform apply (EC2, S3, DynamoDB, **Lambda**, IAM) | | ✅ |
| Push ECR + deploy da API via SSM + smoke test HTTPS | | ✅ |

Qualquer achado HIGH/CRITICAL, segredo vazado ou erro de SAST **barra** a pipeline antes do deploy.
A URL pública aparece no final do job *Smoke test* e em `terraform output url_api` (`https://<ip-com-hifens>.sslip.io/api/v1/mapa`).

### 4.1 Publicar o mapa (carga inicial)
```bash
scripts/publicar_mapa.sh caminho/mapa_cidadao.json
curl -s https://$(terraform -chdir=terraform output -raw site_host)/api/v1/mapa/resumo
```
Depois disso, o Databricks passa a enviar o export diário (ver [docs/ingestao-dados.md](docs/ingestao-dados.md)).

### 5. Observabilidade local (Prometheus + Grafana)
```bash
cp monitoring/.env.example monitoring/.env     # defina a senha do Grafana
scripts/gen-targets.sh <IP_DO_EC2>             # ou sem argumento, lendo o terraform output
docker compose -f monitoring/docker-compose.yml --env-file monitoring/.env up -d
scripts/load.sh <ip-com-hifens>.sslip.io 300  # gera tráfego
```
- Prometheus: http://localhost:9090 (targets e `/alerts`)
- Alertmanager: http://localhost:9093
- Grafana: http://localhost:3000 → pasta *Boca de Lobo* → **4 Sinais de Ouro**

| Sinal | Métrica |
|---|---|
| Latência | `http_request_duration_seconds` (p50/p95/p99) |
| Tráfego | `rate(http_requests_total)` |
| Erros | razão de `http_requests_total{http_status=~"5.."}` |
| Saturação | `process_cpu_seconds_total`, `process_resident_memory_bytes`, `http_requests_in_progress` |

Alertas: `ApiDown`, `DadosDesatualizados` (mapa com mais de 26 h: pipeline de dados parado), `MapaIndisponivel`, `FalhaLeituraDynamo`, `HighErrorRate`, `HighLatencyP95`. Na AWS, o alarme `boca-lobo-lambda-ingestor-erros` (CloudWatch) cobre falhas da Lambda.

### 6. Disparar um alerta
```bash
scripts/trigger-alert.sh $(terraform -chdir=terraform output -raw instance_id)
```
O script para o container via SSM. Após ~1 min o `ApiDown` fica **FIRING** no Prometheus, Alertmanager e Grafana; depois a API é restaurada automaticamente.

### 7. Destruir tudo
```bash
cd terraform
terraform init -backend-config="bucket=<state_bucket_name>"
terraform destroy
```
A role de deploy, o OIDC provider e o bucket do state não são gerenciados pelo Terraform. Para remover a role: `aws iam delete-role-policy --role-name boca-lobo-github-deploy --policy-name boca-lobo-github-deploy && aws iam delete-role --role-name boca-lobo-github-deploy`.

---

## 🔐 Endpoints
| Rota | Descrição |
|---|---|
| `GET /health` | Health check (`mapa_carregado`) |
| `GET /api/v1/mapa` | Retrato atual do mapa do cidadão (mesmo formato do export da Ouro) |
| `GET /api/v1/mapa/resumo` | Contagem de bocas de lobo por nível de alerta |
| `GET /metrics` | Métricas Prometheus |
| `GET /docs` | OpenAPI/Swagger |

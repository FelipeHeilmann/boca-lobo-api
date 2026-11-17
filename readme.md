# 🌊 CitizenTech - Projeto Boca de Lobo (MVP)

> **Global Solution 2026.2 - Secure DevOps Tools & Cloud Computing**

## 🎯 Objetivo do Projeto
O **Boca de Lobo** é um MVP de engenharia de software focado em segurança e emergências urbanas (enchentes). Antes de cada temporal, o sistema cruza o status de obstrução de bueiros/sarjetas com previsões meteorológicas para disparar ações preventivas para a equipe de limpeza e alertas de rota segura para moradores de regiões de risco.

---

## 🛠️ Ficha Técnica
| Tema | Implementação |
|---|---|
| Aplicação | Python 3.11 + FastAPI, métricas Prometheus (4 sinais de ouro) |
| Container | Multi-stage, usuário não-root (uid 10001), sem segredos, `read_only`, `cap_drop: ALL` |
| Execução | Docker Compose no EC2 (`docker-compose.yml`) |
| Nuvem (IaC) | Terraform na AWS: EC2 + SG + EIP + ECR + IAM; state remoto em S3 com lock |
| Acesso admin | **Nenhuma porta admin aberta** — administração via AWS SSM. SSH opcional só para `/32`; `0.0.0.0/0` é rejeitado pela validação |
| CI/CD | GitHub Actions: Gitleaks, Semgrep, Terraform validate, Trivy (config + imagem) a cada push; na `main`: `terraform apply` + deploy via SSM |
| Autenticação na AWS | GitHub OIDC → IAM Role (sem access keys) |
| Observabilidade | Prometheus + Alertmanager + Grafana locais apontando para o MVP publicado |

Documentação complementar:
- [Arquitetura, responsabilidade compartilhada, LGPD e custo](docs/arquitetura.md)
- [Evidências (pipeline barrada/corrigida, dashboard, alerta)](docs/evidencias.md)

### Estrutura
```
app/                     API FastAPI + Dockerfile
docker-compose.yml       Stack executada no EC2 (imagem do ECR)
terraform/bootstrap/     Executado 1x: bucket do state, OIDC do GitHub, IAM Role de deploy
terraform/               Stack principal (aplicada pela pipeline)
.github/workflows/       Pipeline CI/CD
monitoring/              Prometheus, Alertmanager, Grafana (local)
scripts/                 gen-targets, load, trigger-alert
docs/                    Arquitetura e evidências
```

---

## 🚀 Passo a passo reproduzível

### 0. Pré-requisitos
- Conta AWS com permissão de administrador para o bootstrap (IAM, S3, OIDC). *AWS Academy/Learner Lab não permite criar OIDC/roles.*
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) autenticado (`aws sts get-caller-identity`)
- [Terraform ≥ 1.10](https://developer.hashicorp.com/terraform/install)
- Docker + Docker Compose v2
- Fork/clone deste repositório no GitHub

### 1. Rodar localmente (opcional)
```bash
docker build -t boca-de-lobo-api:dev app
IMAGE_URI=boca-de-lobo-api:dev docker compose up -d
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/bocas-risco
curl http://localhost:8000/metrics
docker compose down
```

### 2. Bootstrap da AWS (uma única vez, local)
Cria o bucket do Terraform state, o OIDC provider do GitHub e a IAM Role `boca-lobo-github-deploy`.

> Se você usa um fork, ajuste `github_repository` (formato `owner/repo`) em `terraform/bootstrap/variables.tf` ou passe `-var github_repository=owner/repo`.

```bash
cd terraform/bootstrap
terraform init
terraform apply            # confira o plano e confirme
terraform output           # anote state_bucket_name e github_deploy_role_arn
```

Migre o state do bootstrap para o bucket (assim nenhum state fica local):
```bash
# descomente o bloco backend "s3" em terraform/bootstrap/main.tf e rode:
terraform init -migrate-state -backend-config="bucket=$(terraform output -raw state_bucket_name)"
rm -f terraform.tfstate terraform.tfstate.backup
cd ../..
```

> Se a conta já tiver o OIDC provider do GitHub (`token.actions.githubusercontent.com`), importe-o antes do apply:
> `terraform import aws_iam_openid_connect_provider.github arn:aws:iam::<CONTA>:oidc-provider/token.actions.githubusercontent.com`

### 3. Configurar o GitHub
Em **Settings → Environments → New environment** crie `production` (em *Deployment branches* restrinja a `main`). Dentro dele cadastre:

| Nome | Tipo | Valor |
|---|---|---|
| `AWS_ROLE_ARN` | Secret | output `github_deploy_role_arn` |
| `TF_STATE_BUCKET` | Secret | output `state_bucket_name` |
| `TF_VAR_admin_cidrs` | Secret *(opcional)* | `["SEU.IP.PUBLICO/32"]` para liberar SSH; omita para usar apenas SSM |
| `AWS_REGION` | Variable | `us-east-1` |
| `TF_VAR_project` | Variable *(opcional)* | `boca-de-lobo` |

Não é necessário cadastrar `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`: a pipeline usa OIDC.

### 4. Deploy
```bash
git push origin main
```
A pipeline (`.github/workflows/ci-cd.yml`) executa:

| Job | Em todo push | Só na `main` |
|---|---|---|
| Gitleaks | ✅ | |
| Semgrep | ✅ | |
| Terraform fmt/validate + Trivy config | ✅ | |
| Docker build + Trivy image | ✅ | |
| Terraform apply | | ✅ |
| Push ECR + deploy via SSM + smoke test | | ✅ |

Qualquer achado HIGH/CRITICAL, segredo vazado ou erro de SAST **barra** a pipeline antes do deploy.
A URL pública aparece no final do job *Smoke test* e em `terraform output url_api`.

### 5. Observabilidade local (Prometheus + Grafana)
```bash
cp monitoring/.env.example monitoring/.env     # defina a senha do Grafana
scripts/gen-targets.sh <IP_DO_EC2>             # ou sem argumento, lendo o terraform output
docker compose -f monitoring/docker-compose.yml --env-file monitoring/.env up -d
scripts/load.sh <IP_DO_EC2>:8000 300           # gera tráfego
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

Alertas: `ApiDown`, `PrevisaoChuvaApiDown` (falha silenciosa da Open-Meteo), `HighErrorRate`, `HighLatencyP95`.

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
cd bootstrap && terraform destroy   # esvazie o bucket (versões) antes, se necessário
```

---

## 🔐 Endpoints
| Rota | Descrição |
|---|---|
| `GET /health` | Health check |
| `GET /api/v1/bocas-risco` | Risco de alagamento por ponto monitorado |
| `GET /metrics` | Métricas Prometheus |
| `GET /docs` | OpenAPI/Swagger |

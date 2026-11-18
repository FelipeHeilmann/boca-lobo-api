# 🌊 CitizenTech - Projeto Boca de Lobo (MVP)

> **Global Solution 2026.2 - Secure DevOps Tools & Cloud Computing**

## 🎯 Objetivo do Projeto
O **Boca de Lobo** é um MVP de engenharia de software focado em segurança e emergências urbanas (enchentes). Antes de cada temporal, o sistema cruza o status de obstrução de bueiros/sarjetas com previsões meteorológicas para disparar ações preventivas para a equipe de limpeza e alertas de rota segura para moradores de regiões de risco.

## 🔗 Acesse a aplicação
| Acesso | Link | O que é |
|---|---|---|
| Mapa do cidadão | [felipevoidela.github.io/GS2-2026-Big-Data-Architecture](https://felipevoidela.github.io/GS2-2026-Big-Data-Architecture/) | Frontend que consome a API e mostra onde não passar hoje |
| Swagger (OpenAPI) | [98-86-45-71.sslip.io/docs](https://98-86-45-71.sslip.io/docs) | Documentação interativa; permite testar as rotas pelo navegador |
| Mapa (JSON) | [/api/v1/mapa](https://98-86-45-71.sslip.io/api/v1/mapa) | Retrato atual servido ao frontend |
| Resumo | [/api/v1/mapa/resumo](https://98-86-45-71.sslip.io/api/v1/mapa/resumo) | Contagem de bocas de lobo por nível de alerta |
| Limpeza (RPA) | [/api/v1/rpa](https://98-86-45-71.sslip.io/api/v1/rpa) | Última execução do bot RPA e as ordens de serviço |
| Health check | [/health](https://98-86-45-71.sslip.io/health) | Estado da API e do carregamento do mapa |
| Métricas | [/metrics](https://98-86-45-71.sslip.io/metrics) | Métricas Prometheus coletadas pelo Grafana |

---

<a id="arquitetura"></a>
## 🏗️ Arquitetura

![Diagrama de arquitetura na AWS](docs/arquitetura.png)

> Fonte editável do diagrama: [`docs/arquitetura.drawio`](docs/arquitetura.drawio) (draw.io). No desenho, a Lambda aparece com o rótulo do bucket; o nome real do recurso no Terraform é `boca-lobo-lambda-ingestor`.

### Fluxo de dados
O bucket de ingestão tem **uma pasta por fluxo**, todas com a mesma estrutura, e cada fluxo tem a sua tabela no DynamoDB:

| Pasta do fluxo | Quem envia (usuário IAM) | Conteúdo | Tabela DynamoDB (item) | Rota da API |
|---|---|---|---|---|
| `databricks/` | Databricks (`boca-lobo-databricks`) | `mapa_cidadao.json`, export da camada Ouro | `boca-lobo-dynamo-mapa` (`mapa_atual`) | `/api/v1/mapa` |
| `rpa/` | Bot RPA (`boca-lobo-rpa`) | resultado do bot: `execucao` + `ordens_servico` | `boca-lobo-dynamo-rpa` (`rpa_atual`) | `/api/v1/rpa` |

```
s3://boca-lobo-s3-ingestao/
├── databricks/  entrada/  processado/ (90 dias)  quarentena/ (30 dias)
└── rpa/         entrada/  processado/ (90 dias)  quarentena/ (30 dias)
```

1. Cada fluxo grava o arquivo `.json` em `<fluxo>/entrada/`. Cada usuário IAM só pode gravar na `entrada/` do seu fluxo.
2. O S3 emite o evento **object created** (só para `<fluxo>/entrada/*.json`), que invoca a Lambda `boca-lobo-lambda-ingestor`.
3. A Lambda descobre o fluxo pela pasta e aplica a validação dele (tamanho máximo de 5 MB, coordenadas dentro da Região Metropolitana de São Paulo e IDs duplicados nos dois):
   - **databricks:** estrutura, campos obrigatórios e níveis de alerta (`EVITE A AREA`, `ATENCAO`, `NORMAL`);
   - **rpa:** execução (`id`, `inicio`, `status`, nível `NORMAL`/`ATENCAO`/`ALERTA`, contadores ≥ 0) e ordens de serviço (obstrução e nota de 0 a 100, prioridade `CRITICA`/`ALTA`/`MEDIA`, status `ABERTA`/`AGUARDANDO_REFORCO`). Só a execução e as ordens seguem para o DynamoDB: alertas, contatos mascarados e textos livres do bot ficam de fora (minimização/LGPD).
4. **Arquivo válido:** grava o item do fluxo na tabela dele com escrita condicional (um arquivo mais antigo nunca sobrescreve um mais novo) e move o original para `<fluxo>/processado/`. **Arquivo inválido:** vai para `<fluxo>/quarentena/` com o motivo nos metadados, e a API continua com a última versão boa.
5. A **API no EC2** lê as duas tabelas a cada 60 s e mantém o resultado em cache na memória. Se uma leitura falhar, continua servindo a última versão válida daquele fluxo, sem afetar o outro.
6. O **portal** consulta a API por HTTPS: a aba *Onde não passar* desenha o mapa do cidadão e a aba *Limpeza (RPA)* mostra a última execução do bot e a lista de bocas de lobo para limpar.

### Componentes
| Componente (no diagrama) | Serviço | Papel na solução |
|---|---|---|
| `data-bricks` | Databricks (fora da AWS) | Executa os notebooks Bronze/Prata/Ouro e gera o `mapa_cidadao.json` com as bocas de lobo e o nível de alerta de cada uma |
| `boca-lobo-databricks` | IAM User | Identidade técnica do Databricks; só pode gravar (`s3:PutObject`) em `databricks/entrada/` |
| `s3: putObject` | IAM Policy | Política de menor privilégio dos usuários técnicos: não leem, não listam e não apagam nada |
| `boca-lobo-rpa` | IAM User | Identidade técnica do bot RPA; só pode gravar (`s3:PutObject`) em `rpa/entrada/` |
| `boca-lobo-s3-ingestao` | Amazon S3 | Bucket privado, versionado e criptografado, com uma pasta por fluxo (`databricks/`, `rpa/`); cada uma recebe em `entrada/`, guarda o histórico em `processado/` (90 dias) e os recusados em `quarentena/` (30 dias); exige HTTPS |
| `object created` | Evento S3 | Notificação `s3:ObjectCreated` filtrada por `databricks/entrada/*.json` e `rpa/entrada/*.json`, que dispara a Lambda |
| Lambda | AWS Lambda | `boca-lobo-lambda-ingestor` (Python 3.12, arm64, 256 MB): identifica o fluxo pela pasta, valida o JSON, publica na tabela do fluxo e move o arquivo |
| `boca-lobo-dynamo-mapa` | Amazon DynamoDB | Fluxo databricks. Tabela provisionada (5 RCU / 12 WCU) com um único item `mapa_atual` contendo os pontos compactados em gzip (~11 KB) |
| `boca-lobo-dynamo-rpa` | Amazon DynamoDB | Fluxo rpa. Tabela provisionada (5 RCU / 5 WCU) com um único item `rpa_atual`: última execução do bot e ordens de serviço compactadas em gzip (~2 KB). Somadas, as duas tabelas ficam no Always Free (10 RCU / 17 WCU de 25) |
| `CitizenTech-BocaDeLobo-MVP` | Amazon EC2 | `t3.micro` (Ubuntu 24.04) com Elastic IP, executando os containers Caddy e API; lê o DynamoDB com a IAM Role da instância |
| `portal` | Frontend web | MVP (GitHub Pages) que consome a API via HTTPS: mapa do cidadão e aba de limpeza do bot RPA |

Recursos de apoio que não aparecem no diagrama: repositórios **ECR** (imagens da API e do proxy, com scan na publicação e retenção das 5 últimas), **CloudWatch Logs** e o alarme `boca-lobo-lambda-ingestor-erros`, **X-Ray** na Lambda, **AWS Systems Manager** (deploy e administração sem SSH) e o bucket do Terraform state.

### Decisões de arquitetura
- **Cache com atualização em segundo plano:** o DynamoDB recebe cerca de uma leitura por minuto, independentemente do número de visitantes, o que mantém a tabela no Always Free mesmo em picos de acesso durante uma tempestade.
- **DynamoDB provisionado em vez de on-demand:** 5 RCU e 12 WCU ficam abaixo do limite gratuito permanente de 25 RCU/25 WCU por conta.
- **Validação na entrada:** dados quebrados vindos do pipeline não chegam ao cidadão; ficam isolados em quarentena para análise.
- **HTTPS sem domínio próprio:** o sslip.io resolve `98-86-45-71.sslip.io` para o Elastic IP, e o Caddy emite o certificado Let's Encrypt automaticamente.

### Segurança por camada
| Camada | Controles aplicados |
|---|---|
| Containers | Multi-stage `python:3.11-slim`, usuário não-root (API uid 10001, proxy uid 10002), `read_only`, `cap_drop: ALL`, `no-new-privileges`, limites de memória/CPU/processos, nenhum segredo na imagem |
| Rede | Security Group só com 443 (HTTPS) e 80 (ACME e redirect); a API só é acessível pelo proxy; nenhuma porta admin aberta (administração via SSM) |
| Instância | IMDSv2 obrigatório, EBS criptografado, `unattended-upgrades` |
| HTTP | HSTS, `X-Content-Type-Options`, `Referrer-Policy`; CORS só para a origem do frontend e só `GET` |
| Identidade | Menor privilégio por identidade (EC2 só `GetItem`, Lambda só o necessário, Databricks só `PutObject` em `databricks/entrada/`, bot RPA só `PutObject` em `rpa/entrada/`); pipeline via GitHub OIDC, sem access keys |
| Dados | S3 privado, versionado, criptografado e negando tráfego sem TLS; DynamoDB criptografado; camada Ouro anonimizada (LGPD) |

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
- [Arquitetura, fluxo de dados e segurança](#arquitetura) (nesta página)
- [Ingestão do mapa: Databricks → S3 → Lambda → DynamoDB](docs/ingestao-dados.md)
- [Pré-requisitos AWS: role de deploy, OIDC e bucket do state](docs/aws-role.md)
- [Evidências (pipeline barrada/corrigida, dashboard, alerta)](docs/evidencias.md)

### Estrutura
```
app/                     API FastAPI + Dockerfile + testes          → container no EC2
lambda/ingestor/         Lambda que valida o JSON e grava no Dynamo → publicada pelo Terraform
proxy/                   Imagem do Caddy (Caddyfile embutido) — proxy HTTPS (Let's Encrypt)
docker-compose.yml       Stack executada no EC2 (proxy + api)
terraform/               main.tf (EC2/SG/EIP/ECR) + data.tf (S3/Lambda/DynamoDB/IAM)
.github/workflows/       Pipeline CI/CD única do monorepo
monitoring/              Prometheus, Alertmanager, Grafana (local)
scripts/                 publicar_mapa, gen-targets, load, trigger-alert
docs/                    Diagrama de arquitetura (PNG + fonte draw.io)
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

### 4.2 Credencial do bot RPA
O Terraform cria o usuário `boca-lobo-rpa`, mas **não** a access key (o segredo ficaria no state). Gere pela CLI e cadastre direto nos GitHub Secrets do repositório do bot:
```bash
aws iam create-access-key --user-name boca-lobo-rpa --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text
```
| Secret (repo do bot) | Valor |
|---|---|
| `RPA_AWS_ACCESS_KEY_ID` | Access key ID gerada acima |
| `RPA_AWS_SECRET_ACCESS_KEY` | Secret access key gerada acima |

O bot grava o resultado em `s3://boca-lobo-s3-ingestao/rpa/entrada/<nome>.json` (um arquivo por execução, com `execucao` e `ordens_servico` na raiz). A Lambda publica em até alguns segundos e a API passa a servir em `/api/v1/rpa` em até 60 s.

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
| `GET /health` | Health check (`mapa_carregado`, `rpa_carregado`) |
| `GET /api/v1/mapa` | Retrato atual do mapa do cidadão (mesmo formato do export da Ouro) |
| `GET /api/v1/mapa/resumo` | Contagem de bocas de lobo por nível de alerta |
| `GET /api/v1/rpa` | Última execução do bot RPA (nível e chuva prevista, contadores, status) e as ordens de serviço, da mais urgente para a menos urgente |
| `GET /api/v1/rpa/resumo` | Ordens de serviço por prioridade e por status, com o nível de chuva da execução |
| `GET /metrics` | Métricas Prometheus |
| `GET /docs` | OpenAPI/Swagger |

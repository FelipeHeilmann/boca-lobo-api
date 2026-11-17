# Evidências

> Preencha com links das execuções do GitHub Actions e prints (salve as imagens em `docs/img/`).

## 1. Pipeline barrada

### 1a. Semgrep — actions com tag mutável (supply chain)
- **Run:** <link da execução>
- **Job que barrou:** `Semgrep (SAST)`, regra `github-actions-mutable-action-tag` (14 achados)
- **Motivo:** `uses: actions/checkout@v4` etc. podem ser reapontados pelo dono da action (caso trivy-action)
- **Correção:** todas as actions fixadas por SHA de 40 caracteres (`# vX.Y.Z` em comentário) + Dependabot para atualizá-las
- Print: `![semgrep barrou](img/semgrep-barrado.png)`

### 1b. Trivy — dependências vulneráveis

- **Commit:** `<sha>` — dependências desatualizadas (`fastapi==0.110.0` → starlette 0.36.x, `requests==2.31.0`)
- **Run:** <link da execução>
- **Job que barrou:** `Docker build + Trivy image`
- **Motivo:** CVEs HIGH com correção disponível (ex.: starlette CVE-2024-47874)
- Print: `![pipeline barrada](img/pipeline-barrada.png)`

## 2. Pipeline corrigida

- **Commit:** `<sha>` — atualização do `app/requirements.txt` para:
  ```
  fastapi==0.163.1
  uvicorn==0.54.0
  prometheus-client==0.26.0
  requests==2.34.2
  ```
  (validado com `pip-audit`: *No known vulnerabilities found*)
- **Run:** <link da execução>
- Todos os jobs verdes: Gitleaks, Semgrep, Terraform + Trivy config, Build + Trivy image, Terraform apply, Deploy
- Smoke test: `curl http://<EIP>:8000/health` → 200
- Print: `![pipeline corrigida](img/pipeline-corrigida.png)`

## 3. MVP publicado

- URL: `http://<EIP>:8000/api/v1/bocas-risco`
- Print do Security Group (sem porta admin / sem 0.0.0.0/0 em porta admin): `![sg](img/security-group.png)`

## 4. Dashboard — 4 sinais de ouro

- Print do Grafana com latência, tráfego, erros e saturação: `![dashboard](img/grafana-golden-signals.png)`

## 5. Alerta disparado

- Comando: `scripts/trigger-alert.sh <instance-id>`
- Alerta `ApiDown` em FIRING no Prometheus (`/alerts`), no Alertmanager e no painel do Grafana
- Prints: `![alerta prometheus](img/alerta-prometheus.png)` · `![alertmanager](img/alertmanager.png)`

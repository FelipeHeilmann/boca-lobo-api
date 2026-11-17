# Evidências

> Preencha com links das execuções do GitHub Actions e prints (salve as imagens em `docs/img/`).

## 1. Pipeline barrada

### 1a. Semgrep — actions com tag mutável (supply chain)
- **Run:** <link da execução>
- **Job que barrou:** `Semgrep (SAST)`, regra `github-actions-mutable-action-tag` (14 achados)
- **Motivo:** `uses: actions/checkout@v4` etc. podem ser reapontados pelo dono da action (caso trivy-action)
- **Correção:** todas as actions fixadas por SHA de 40 caracteres (`# vX.Y.Z` em comentário) + Dependabot para atualizá-las
- Print: `![semgrep barrou](img/semgrep-barrado.png)`

### 1b. Trivy — imagem com vulnerabilidades HIGH
- **Commit:** `d67fe95` · **Run:** <link da execução>
- **Job que barrou:** `Docker build + Trivy image` — 9 achados HIGH:

| Origem | Pacote | CVE | Correção aplicada |
|---|---|---|---|
| Dependência da app | starlette 0.36.3 (via fastapi 0.110.0) | CVE-2024-47874, CVE-2026-48818, CVE-2026-54283 | `fastapi==0.163.1` (starlette ≥ 1.3.1) |
| Ferramentas de build na imagem | setuptools 70.3.0, wheel 0.45.1, jaraco.context 5.3.0 | CVE-2025-47273, CVE-2026-24049, CVE-2026-23949 | `pip uninstall setuptools wheel pip` no venv e no Python do sistema (não usados em runtime) |
| SO da imagem base (Debian 13) | zlib1g | CVE-2026-85091 | `apt-get upgrade` no estágio final |

- Print: `![trivy barrou](img/trivy-barrado.png)`

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

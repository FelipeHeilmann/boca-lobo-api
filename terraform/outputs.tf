output "instance_id" {
  description = "ID da instância (alvo do deploy via SSM)"
  value       = aws_instance.app.id
}

output "public_ip" {
  description = "IP público fixo (Elastic IP) do MVP"
  value       = aws_eip.app.public_ip
}

output "ecr_repository_url" {
  description = "URL do repositório ECR da imagem"
  value       = aws_ecr_repository.app.repository_url
}

output "proxy_ecr_repository_url" {
  description = "URL do repositório ECR da imagem do proxy (Caddy)"
  value       = aws_ecr_repository.proxy.repository_url
}

output "site_host" {
  description = "Hostname público com HTTPS (sslip.io resolve para o Elastic IP)"
  value       = local.site_host
}

output "url_api" {
  description = "URL pública da API do mapa"
  value       = "https://${local.site_host}/api/v1/mapa"
}

output "url_metrics" {
  description = "URL pública de métricas do Prometheus"
  value       = "https://${local.site_host}/metrics"
}

output "dynamodb_table" {
  description = "Tabela DynamoDB do fluxo databricks (mapa) lida pela API"
  value       = aws_dynamodb_table.mapa.name
}

output "dynamodb_table_rpa" {
  description = "Tabela DynamoDB do fluxo rpa (última execução e ordens de serviço) lida pela API"
  value       = aws_dynamodb_table.rpa.name
}

output "ingestao_bucket" {
  description = "Bucket de ingestão: uma pasta por fluxo (databricks/, rpa/), cada uma com entrada/, processado/ e quarentena/"
  value       = aws_s3_bucket.ingestao.bucket
}

output "databricks_user" {
  description = "Usuário IAM (somente PutObject em databricks/entrada/) para o Databricks"
  value       = aws_iam_user.databricks.name
}

output "rpa_user" {
  description = "Usuário IAM (somente PutObject em rpa/entrada/) para o bot RPA"
  value       = aws_iam_user.rpa.name
}

locals {
  site_host = "${replace(aws_eip.app.public_ip, ".", "-")}.sslip.io"
}

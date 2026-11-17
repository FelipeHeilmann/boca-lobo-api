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
  description = "Tabela DynamoDB lida pela API"
  value       = aws_dynamodb_table.mapa.name
}

output "ingestao_bucket" {
  description = "Bucket que recebe o export da camada Ouro (prefixo entrada/)"
  value       = aws_s3_bucket.ingestao.bucket
}

output "databricks_user" {
  description = "Usuário IAM (somente PutObject em entrada/) para o Databricks"
  value       = aws_iam_user.databricks.name
}

locals {
  site_host = "${replace(aws_eip.app.public_ip, ".", "-")}.sslip.io"
}

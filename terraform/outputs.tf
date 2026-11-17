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

output "url_api" {
  description = "URL pública oficial do MVP no ar"
  value       = "http://${aws_eip.app.public_ip}:8000/api/v1/bocas-risco"
}

output "url_metrics" {
  description = "URL pública de métricas do Prometheus"
  value       = "http://${aws_eip.app.public_ip}:8000/metrics"
}

variable "aws_region" {
  type        = string
  description = "Região da AWS utilizada para o provisionamento"
  default     = "us-east-1"
}

variable "project" {
  type        = string
  description = "Prefixo/tag dos recursos (deve bater com o escopo da role de deploy)"
  default     = "boca-de-lobo"
}

variable "instance_type" {
  type        = string
  description = "Tipo da instância EC2"
  default     = "t3.micro"
}

variable "admin_cidrs" {
  type        = list(string)
  description = "CIDRs autorizados a SSH (opcional). Vazio = nenhuma porta admin aberta; acesso via SSM."
  default     = []

  validation {
    condition     = alltrue([for c in var.admin_cidrs : !contains(["0.0.0.0/0", "::/0"], c)])
    error_message = "Porta administrativa nunca pode ser aberta para 0.0.0.0/0 ou ::/0."
  }

  validation {
    condition     = alltrue([for c in var.admin_cidrs : can(cidrhost(c, 0))])
    error_message = "Cada item de admin_cidrs deve ser um CIDR válido (ex.: 200.100.50.25/32)."
  }
}

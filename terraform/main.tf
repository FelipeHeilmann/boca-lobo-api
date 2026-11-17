terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # State remoto em S3 (fora do repositório), com lock nativo.
  # O bucket é informado na pipeline: terraform init -backend-config="bucket=$TF_STATE_BUCKET"
  backend "s3" {
    key          = "boca-de-lobo/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}

# ------------------------------- Rede ---------------------------------------
data "aws_vpc" "default" {
  default = true
}

# Ubuntu 24.04 LTS oficial da Canonical (já inclui o SSM Agent)
data "aws_ssm_parameter" "ubuntu_ami" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

resource "aws_security_group" "app" {
  name        = "${var.project}-sg"
  description = "MVP Boca de Lobo - somente porta da API publica; admin via SSM"
  vpc_id      = data.aws_vpc.default.id

  # API pública do MVP na porta 80 (também raspada pelo Prometheus local em /metrics)
  ingress {
    description = "API HTTP publica do MVP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # trivy:ignore:AVD-AWS-0107
  }

  # Porta admin (SSH) só é criada se admin_cidrs for informado — e NUNCA 0.0.0.0/0
  # (bloqueado pela validation em variables.tf). O padrão é acesso via SSM, sem porta aberta.
  dynamic "ingress" {
    for_each = length(var.admin_cidrs) > 0 ? [1] : []
    content {
      description = "SSH administrativo restrito"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.admin_cidrs
    }
  }

  egress {
    description = "Saida para Open-Meteo, ECR, SSM e pacotes do SO"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"] # trivy:ignore:AVD-AWS-0104
  }
}

# ------------------------------- ECR ----------------------------------------
resource "aws_ecr_repository" "app" {
  name                 = var.project
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256" # trivy:ignore:AVD-AWS-0033
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Mantem apenas as 5 imagens mais recentes"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

# -------------------------- IAM da instância --------------------------------
data "aws_iam_policy_document" "ec2_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "boca-lobo-ec2"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ecr_read" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_instance_profile" "ec2" {
  name = "boca-lobo-ec2"
  role = aws_iam_role.ec2.name
}

# ------------------------------- EC2 ----------------------------------------
resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.ubuntu_ami.value
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name
  user_data              = file("${path.module}/user_data.sh")

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 obrigatório
    http_put_response_hop_limit = 2          # permite containers acessarem o IMDS do host com IMDSv2
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 8
    encrypted   = true
  }

  lifecycle {
    # Novas AMIs não devem recriar a instância a cada apply
    ignore_changes = [ami, user_data]
  }

  tags = {
    Name = "CitizenTech-BocaDeLobo-MVP"
  }
}

resource "aws_eip" "app" {
  instance = aws_instance.app.id
  domain   = "vpc"
}

# -----------------------------------------------------------------------------
# Camada de dados do MVP:
#   Databricks --(PutObject)--> S3 entrada/ --(evento)--> Lambda ingestor --> DynamoDB <-- API (EC2)
# Tudo dentro do Always Free (Lambda, DynamoDB provisionado <= 25 RCU/WCU, CloudWatch);
# o S3 custa frações de centavo por mês.
# -----------------------------------------------------------------------------
data "aws_caller_identity" "current" {}

locals {
  # Padrão de nomes: boca-lobo-<serviço>-<função>
  ingestao_bucket = "boca-lobo-s3-ingestao"
  tabela_mapa     = "boca-lobo-dynamo-mapa"
  lambda_nome     = "boca-lobo-lambda-ingestor"
}

# ------------------------------- S3 de ingestão ------------------------------
resource "aws_s3_bucket" "ingestao" {
  bucket        = local.ingestao_bucket
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "ingestao" {
  bucket                  = aws_s3_bucket.ingestao.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "ingestao" {
  bucket = aws_s3_bucket.ingestao.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "ingestao" {
  bucket = aws_s3_bucket.ingestao.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ingestao" {
  bucket = aws_s3_bucket.ingestao.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256" # trivy:ignore:AVD-AWS-0132
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "ingestao" {
  bucket = aws_s3_bucket.ingestao.id

  rule {
    id     = "historico-processado-90d"
    status = "Enabled"
    filter {
      prefix = "processado/"
    }
    expiration {
      days = 90
    }
  }

  rule {
    id     = "quarentena-30d"
    status = "Enabled"
    filter {
      prefix = "quarentena/"
    }
    expiration {
      days = 30
    }
  }

  rule {
    id     = "versoes-antigas-30d"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "ingestao_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.ingestao.arn, "${aws_s3_bucket.ingestao.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "ingestao" {
  bucket     = aws_s3_bucket.ingestao.id
  policy     = data.aws_iam_policy_document.ingestao_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.ingestao]
}

# --------------------------------- DynamoDB ----------------------------------
# PROVISIONED (não on-demand) para ficar no Always Free: 25 RCU/WCU por conta.
# Um único item "mapa_atual" com os pontos compactados (~11 KB): ~11 WCU por carga, ~2 RCU por leitura.
# Exceção aceita (Semgrep aws-dynamodb-table-unencrypted): a tabela É criptografada com a chave gerenciada
# pela AWS (aws/dynamodb). Uma KMS CMK própria custaria US$ 1/mês e tiraria a camada de dados do Always Free;
# o conteúdo é a view Ouro, já anonimizada (sem dados pessoais).
resource "aws_dynamodb_table" "mapa" { # nosemgrep: terraform.aws.security.aws-dynamodb-table-unencrypted.aws-dynamodb-table-unencrypted
  name           = local.tabela_mapa
  billing_mode   = "PROVISIONED"
  read_capacity  = 5
  write_capacity = 12
  hash_key       = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  server_side_encryption {
    enabled = true # chave gerenciada pela AWS (aws/dynamodb), sem custo mensal
  }

  deletion_protection_enabled = false
}

# ---------------------------------- Lambda -----------------------------------
data "archive_file" "ingestor" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ingestor/src"
  output_path = "${path.module}/.build/ingestor.zip"
  excludes    = ["__pycache__"]
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingestor" {
  name               = "boca-lobo-lambda-ingestor"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "ingestor" {
  statement {
    sid       = "LerEntrada"
    actions   = ["s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.ingestao.arn}/entrada/*"]
  }
  statement {
    sid       = "GravarDestinos"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.ingestao.arn}/processado/*", "${aws_s3_bucket.ingestao.arn}/quarentena/*"]
  }
  statement {
    sid       = "PublicarMapa"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.mapa.arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingestor.arn}:*"]
  }
  statement {
    sid       = "XRay"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray não suporta restrição por recurso nessas ações
  }
}

resource "aws_iam_role_policy" "ingestor" {
  name   = "boca-lobo-lambda-ingestor"
  role   = aws_iam_role.ingestor.id
  policy = data.aws_iam_policy_document.ingestor.json
}

resource "aws_cloudwatch_log_group" "ingestor" {
  name              = "/aws/lambda/${local.lambda_nome}"
  retention_in_days = 14 # trivy:ignore:AVD-AWS-0017
}

resource "aws_lambda_function" "ingestor" {
  function_name    = local.lambda_nome
  description      = "Valida o export da camada Ouro (S3 entrada/) e publica o mapa no DynamoDB"
  role             = aws_iam_role.ingestor.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.ingestor.output_path
  source_code_hash = data.archive_file.ingestor.output_base64sha256
  memory_size      = 256
  timeout          = 30

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.mapa.name
    }
  }

  tracing_config {
    mode = "Active" # X-Ray: Always Free de 100 mil traces/mês
  }

  depends_on = [aws_cloudwatch_log_group.ingestor, aws_iam_role_policy.ingestor]
}

resource "aws_lambda_permission" "s3_invoca_ingestor" {
  statement_id   = "PermitirS3Ingestao"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.ingestor.function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.ingestao.arn
  source_account = data.aws_caller_identity.current.account_id
}

resource "aws_s3_bucket_notification" "ingestao" {
  bucket = aws_s3_bucket.ingestao.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingestor.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "entrada/"
    filter_suffix       = ".json"
  }

  depends_on = [aws_lambda_permission.s3_invoca_ingestor]
}

# Alarme (Always Free: 10 alarmes) — qualquer erro não tratado na ingestão
resource "aws_cloudwatch_metric_alarm" "ingestor_erros" {
  alarm_name          = "${local.lambda_nome}-erros"
  alarm_description   = "A Lambda de ingestão falhou: o mapa não foi atualizado."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.ingestor.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
}

# --------------------------- Acesso da API (EC2) -----------------------------
data "aws_iam_policy_document" "ec2_ler_mapa" {
  statement {
    sid       = "LerMapa"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.mapa.arn]
  }
}

resource "aws_iam_role_policy" "ec2_ler_mapa" {
  name   = "boca-lobo-ec2-ler-mapa"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.ec2_ler_mapa.json
}

# ------------------------- Envio a partir do Databricks ----------------------
# Usuário técnico que SÓ pode gravar em entrada/. A access key NÃO é criada aqui (o segredo
# ficaria no Terraform state): é gerada pela CLI e guardada direto no secret scope do Databricks.
resource "aws_iam_user" "databricks" {
  name = "boca-lobo-databricks"
}

data "aws_iam_policy_document" "databricks" {
  statement {
    sid       = "EnviarExportOuro"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.ingestao.arn}/entrada/*"]
  }
}

resource "aws_iam_user_policy" "databricks" {
  name   = "boca-lobo-databricks-envio"
  user   = aws_iam_user.databricks.name
  policy = data.aws_iam_policy_document.databricks.json
}

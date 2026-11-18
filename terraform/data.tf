# -----------------------------------------------------------------------------
# Camada de dados do MVP — uma pasta por fluxo no bucket, uma tabela por fluxo no DynamoDB:
#   Databricks --(PutObject)--> S3 databricks/entrada/ --(evento)--> Lambda ingestor --> DynamoDB mapa --> API (EC2)
#   Bot RPA    --(PutObject)--> S3 rpa/entrada/        --(evento)--> Lambda ingestor --> DynamoDB rpa  --> API (EC2)
# Cada fluxo tem <fluxo>/entrada/ (recebe), <fluxo>/processado/ (aceitos, 90 dias) e <fluxo>/quarentena/ (recusados,
# 30 dias). A Lambda descobre o fluxo pela pasta, aplica a validação daquele fluxo e grava na tabela dele.
# Tudo dentro do Always Free (Lambda, DynamoDB provisionado <= 25 RCU/WCU, CloudWatch);
# o S3 custa frações de centavo por mês.
# -----------------------------------------------------------------------------
data "aws_caller_identity" "current" {}

locals {
  # Padrão de nomes: boca-lobo-<serviço>-<função>
  ingestao_bucket = "boca-lobo-s3-ingestao"
  tabela_mapa     = "boca-lobo-dynamo-mapa"
  tabela_rpa      = "boca-lobo-dynamo-rpa"
  lambda_nome     = "boca-lobo-lambda-ingestor"

  # Fluxos de dados que chegam ao bucket (nome da pasta = nome do fluxo; ver lambda/ingestor/src/handler.py)
  fluxos = ["databricks", "rpa"]

  # Retenção por pasta. As duas últimas são as pastas da estrutura antiga (antes de existir uma pasta por fluxo):
  # ficam só para expirar o histórico que já estava lá.
  retencao = merge(
    { for f in local.fluxos : "${f}-processado-90d" => { prefixo = "${f}/processado/", dias = 90 } },
    { for f in local.fluxos : "${f}-quarentena-30d" => { prefixo = "${f}/quarentena/", dias = 30 } },
    {
      "legado-processado-90d" = { prefixo = "processado/", dias = 90 }
      "legado-quarentena-30d" = { prefixo = "quarentena/", dias = 30 }
    },
  )
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

  dynamic "rule" {
    for_each = local.retencao
    content {
      id     = rule.key
      status = "Enabled"
      filter {
        prefix = rule.value.prefixo
      }
      expiration {
        days = rule.value.dias
      }
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
# Uma tabela por fluxo. PROVISIONED (não on-demand) para ficar no Always Free: 25 RCU/WCU por conta, somando
# as duas tabelas (mapa 5/12 + rpa 5/5 = 10 RCU / 17 WCU).
# Exceção aceita (Semgrep aws-dynamodb-table-unencrypted): as tabelas SÃO criptografadas com a chave gerenciada
# pela AWS (aws/dynamodb). Uma KMS CMK própria custaria US$ 1/mês e tiraria a camada de dados do Always Free;
# o conteúdo não tem dados pessoais (view Ouro anonimizada; do RPA só execução e ordens de serviço).

# Fluxo databricks: um único item "mapa_atual" com os pontos compactados (~11 KB): ~11 WCU por carga, ~2 RCU por leitura.
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

# Fluxo rpa: um único item "rpa_atual" com a última execução do bot e as ordens de serviço compactadas (~2 KB).
resource "aws_dynamodb_table" "rpa" { # nosemgrep: terraform.aws.security.aws-dynamodb-table-unencrypted.aws-dynamodb-table-unencrypted
  name           = local.tabela_rpa
  billing_mode   = "PROVISIONED"
  read_capacity  = 5
  write_capacity = 5
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
    resources = [for f in local.fluxos : "${aws_s3_bucket.ingestao.arn}/${f}/entrada/*"]
  }
  statement {
    sid     = "GravarDestinos"
    actions = ["s3:PutObject"]
    resources = flatten([for f in local.fluxos : [
      "${aws_s3_bucket.ingestao.arn}/${f}/processado/*",
      "${aws_s3_bucket.ingestao.arn}/${f}/quarentena/*",
    ]])
  }
  statement {
    sid       = "PublicarTabelas"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.mapa.arn, aws_dynamodb_table.rpa.arn]
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
  description      = "Valida os arquivos de cada fluxo (S3 <fluxo>/entrada/) e publica na tabela DynamoDB do fluxo"
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
      TABLE_NAME     = aws_dynamodb_table.mapa.name # fluxo databricks
      TABLE_NAME_RPA = aws_dynamodb_table.rpa.name  # fluxo rpa
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

  # Só <fluxo>/entrada/ dispara a Lambda: processado/ e quarentena/ (onde ela mesma grava) nunca reentram.
  dynamic "lambda_function" {
    for_each = toset(local.fluxos)
    content {
      id                  = "ingestao-${lambda_function.value}"
      lambda_function_arn = aws_lambda_function.ingestor.arn
      events              = ["s3:ObjectCreated:*"]
      filter_prefix       = "${lambda_function.value}/entrada/"
      filter_suffix       = ".json"
    }
  }

  depends_on = [aws_lambda_permission.s3_invoca_ingestor]
}

# Alarme (Always Free: 10 alarmes) — qualquer erro não tratado na ingestão
resource "aws_cloudwatch_metric_alarm" "ingestor_erros" {
  alarm_name          = "${local.lambda_nome}-erros"
  alarm_description   = "A Lambda de ingestão falhou: o mapa ou o resultado do RPA não foi atualizado."
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
    sid       = "LerTabelas"
    actions   = ["dynamodb:GetItem"]
    resources = [aws_dynamodb_table.mapa.arn, aws_dynamodb_table.rpa.arn]
  }
}

resource "aws_iam_role_policy" "ec2_ler_mapa" {
  name   = "boca-lobo-ec2-ler-mapa"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.ec2_ler_mapa.json
}

# ------------------------- Envio a partir do Databricks ----------------------
# Usuário técnico que SÓ pode gravar em databricks/entrada/. A access key NÃO é criada aqui (o segredo
# ficaria no Terraform state): é gerada pela CLI e guardada direto no secret scope do Databricks.
resource "aws_iam_user" "databricks" {
  name = "boca-lobo-databricks"
}

data "aws_iam_policy_document" "databricks" {
  statement {
    sid       = "EnviarExportOuro"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.ingestao.arn}/databricks/entrada/*"]
  }
}

resource "aws_iam_user_policy" "databricks" {
  name   = "boca-lobo-databricks-envio"
  user   = aws_iam_user.databricks.name
  policy = data.aws_iam_policy_document.databricks.json
}

# --------------------------- Envio a partir do bot RPA -----------------------
# Mesmo modelo do Databricks: usuário técnico que SÓ pode gravar em rpa/entrada/. A access key NÃO é criada aqui
# (o segredo ficaria no Terraform state): é gerada pela CLI e guardada nos GitHub Secrets do bot (RPA_AWS_*).
resource "aws_iam_user" "rpa" {
  name = "boca-lobo-rpa"
}

data "aws_iam_policy_document" "rpa" {
  statement {
    sid       = "EnviarResultadoRpa"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.ingestao.arn}/rpa/entrada/*"]
  }
}

resource "aws_iam_user_policy" "rpa" {
  name   = "boca-lobo-rpa-envio"
  user   = aws_iam_user.rpa.name
  policy = data.aws_iam_policy_document.rpa.json
}

# O usuário e a política do RPA foram criados pela CLI antes de entrarem no Terraform: estes blocos os trazem
# para o state no próximo apply (sem recriar nada). Depois do primeiro apply podem ser removidos.
import {
  to = aws_iam_user.rpa
  id = "boca-lobo-rpa"
}

import {
  to = aws_iam_user_policy.rpa
  id = "boca-lobo-rpa:boca-lobo-rpa-envio"
}

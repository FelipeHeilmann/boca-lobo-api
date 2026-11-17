# Pré-requisitos AWS (gerenciados em repositório separado)

O OIDC provider do GitHub, a IAM Role de deploy e o bucket S3 do Terraform state **não são criados por este repositório**: eles vivem em um repositório de infraestrutura base separado. Esta pipeline apenas **assume** a role via OIDC.

Use este checklist para conferir se a role externa é compatível com a pipeline.

## 1. OIDC provider
- URL: `https://token.actions.githubusercontent.com`
- Audience (client id): `sts.amazonaws.com`

## 2. Trust policy da role

Os jobs `infra` e `deploy` rodam no environment **`production`**, então o claim `sub` do token é `repo:<owner>/<repo>:environment:production`. Uma trust policy que aceite apenas `ref:refs/heads/main` **não funciona** com jobs que usam environment.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::<CONTA>:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:CarlosECRibeiro/boca_de_lobo_fiap:environment:production"
      }
    }
  }]
}
```

## 3. Permissões mínimas da role

Substitua `<CONTA>`, `<BUCKET>` e a região, se necessário. Os nomes abaixo são os que o Terraform deste repositório cria:
- ECR `boca-de-lobo`
- role/instance profile do EC2 `boca-lobo-ec2`
- recursos com a tag `Project=boca-de-lobo`

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "StateBucketList", "Effect": "Allow", "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::<BUCKET>" },
    { "Sid": "StateObjects", "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::<BUCKET>/boca-de-lobo/*" },

    { "Sid": "Ec2InRegion", "Effect": "Allow", "Action": "ec2:*", "Resource": "*",
      "Condition": { "StringEquals": { "aws:RequestedRegion": "us-east-1" } } },

    { "Sid": "EcrAuth", "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*" },
    { "Sid": "EcrRepo", "Effect": "Allow", "Action": "ecr:*",
      "Resource": "arn:aws:ecr:us-east-1:<CONTA>:repository/boca-de-lobo*" },
    { "Sid": "EcrCreateDescribe", "Effect": "Allow",
      "Action": ["ecr:CreateRepository", "ecr:DescribeRepositories"], "Resource": "*" },

    { "Sid": "Ec2RoleManagement", "Effect": "Allow",
      "Action": ["iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole", "iam:UntagRole",
                 "iam:UpdateAssumeRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy",
                 "iam:ListAttachedRolePolicies", "iam:ListRolePolicies", "iam:ListInstanceProfilesForRole"],
      "Resource": "arn:aws:iam::<CONTA>:role/boca-lobo-*" },
    { "Sid": "Ec2InstanceProfile", "Effect": "Allow",
      "Action": ["iam:CreateInstanceProfile", "iam:DeleteInstanceProfile", "iam:GetInstanceProfile",
                 "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile",
                 "iam:TagInstanceProfile", "iam:UntagInstanceProfile"],
      "Resource": "arn:aws:iam::<CONTA>:instance-profile/boca-lobo-*" },
    { "Sid": "PassEc2Role", "Effect": "Allow", "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::<CONTA>:role/boca-lobo-ec2",
      "Condition": { "StringEquals": { "iam:PassedToService": "ec2.amazonaws.com" } } },

    { "Sid": "SsmSendCommandDocument", "Effect": "Allow", "Action": "ssm:SendCommand",
      "Resource": "arn:aws:ssm:us-east-1::document/AWS-RunShellScript" },
    { "Sid": "SsmSendCommandInstances", "Effect": "Allow", "Action": "ssm:SendCommand",
      "Resource": "arn:aws:ec2:us-east-1:<CONTA>:instance/*",
      "Condition": { "StringEquals": { "aws:ResourceTag/Project": "boca-de-lobo" } } },
    { "Sid": "SsmRead", "Effect": "Allow",
      "Action": ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations", "ssm:ListCommands",
                 "ssm:DescribeInstanceInformation", "ssm:GetParameter", "ssm:GetParameters"],
      "Resource": "*" }
  ]
}
```

O `s3:DeleteObject` é necessário para o lock nativo do backend S3 (`use_lockfile = true`).

## 4. Bucket do Terraform state
- Privado (block public access), versionado e com criptografia SSE.
- A pipeline grava em `s3://<BUCKET>/boca-de-lobo/terraform.tfstate`.
- O nome do bucket vai no secret `TF_STATE_BUCKET`.

## 5. Sintomas comuns
| Erro na pipeline | Causa provável |
|---|---|
| `Not authorized to perform sts:AssumeRoleWithWebIdentity` | `sub` da trust policy não bate com `repo:...:environment:production` ou repositório diferente |
| `AccessDenied` em `s3:PutObject`/`DeleteObject` no `terraform init/apply` | permissão no prefixo `boca-de-lobo/*` do bucket |
| `iam:PassRole` negado ao criar o EC2 | `PassRole` sem `role/boca-lobo-ec2` |
| `ssm:SendCommand` negado | instância sem a tag `Project=boca-de-lobo` ou policy sem o documento `AWS-RunShellScript` |

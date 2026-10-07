locals {
  image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

  db_base = {
    DB_HOST        = aws_db_instance.main.address
    DB_PORT        = tostring(aws_db_instance.main.port)
    DB_NAME        = var.db_name
    DB_SSLMODE     = "verify-full"
    DB_SSLROOTCERT = "/opt/rds-ca.pem" # baked into the image
  }

  # web, worker and crawl connect as the restricted role refs_app (rows only, no DDL); the password is
  # read at runtime from its secret, never injected once at task start
  db_env = merge(local.db_base, {
    DB_USER       = "refs_app"
    DB_SECRET_ARN = local.secret_arn["db-app-password"]
  })

  # the migrate task connects as the master user (the RDS-managed, rotating secret), applies the schema
  # and creates/updates refs_app from its secret
  migrate_env = merge(local.db_base, {
    DB_USER                = var.db_username
    DB_SECRET_ARN          = local.db_secret
    DB_APP_ROLE_SECRET_ARN = local.secret_arn["db-app-password"]
  })

  # No HTTP_PROXY/HTTPS_PROXY anywhere: the research fetcher vets addresses itself and ignores them.
  common_env = merge(local.db_env, {
    S3_BUCKET          = aws_s3_bucket.originals.bucket
    EXTRACT_MODEL      = var.extract_model
    DRAFT_MODEL        = var.draft_model
    AWS_REGION_NAME    = var.region
    AWS_DEFAULT_REGION = var.region
    LITELLM_LOG        = "WARNING"
  })

  web_env = merge(local.common_env, {
    APP_ORIGIN           = var.app_origin
    OIDC_REDIRECT_URI    = "${var.app_origin}/auth"
    OIDC_METADATA_URL    = var.oidc_metadata_url
    OIDC_CLIENT_ID       = var.oidc_client_id
    OIDC_GROUPS_CLAIM    = var.oidc_groups_claim
    FORWARDED_ALLOW_IPS  = var.vpc_cidr # uvicorn trusts X-Forwarded-* from the ALB only
    SESSION_HTTPS_ONLY   = "true"
    ROLE_USER_GROUPS     = var.role_user_groups
    ROLE_REVIEWER_GROUPS = var.role_reviewer_groups
    ROLE_ADMIN_GROUPS    = var.role_admin_groups
  })

  web_secrets = {
    SESSION_SECRET     = local.secret_arn["session-secret"]
    OIDC_CLIENT_SECRET = local.secret_arn["oidc-client-secret"]
  }

  worker_env = merge(
    local.common_env,
    var.graph_client_id == "" ? {} : { GRAPH_CLIENT_ID = var.graph_client_id },
    var.confluence_email == "" ? {} : { CONFLUENCE_EMAIL = var.confluence_email },
  )

  worker_secrets = {
    GRAPH_CLIENT_SECRET = local.secret_arn["graph-client-secret"]
    CONFLUENCE_TOKEN    = local.secret_arn["confluence-token"]
    BRAVE_API_KEY       = local.secret_arn["brave-api-key"]
  }

  tasks = {
    web = {
      cpu     = var.web_cpu
      memory  = var.web_memory
      command = ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
      env     = local.web_env
      secrets = local.web_secrets
      role    = aws_iam_role.web.arn
      ports   = [{ containerPort = 8000, protocol = "tcp" }]
      stop    = 30
    }
    worker = {
      cpu     = var.worker_cpu
      memory  = var.worker_memory
      command = ["python", "-m", "app.worker"]
      env     = local.worker_env
      secrets = local.worker_secrets
      role    = aws_iam_role.worker.arn
      ports   = []
      stop    = 120 # SIGTERM requeues the running job; give it time
    }
    crawl = {
      cpu     = 256
      memory  = 512
      command = ["python", "-m", "app.enqueue_crawls"]
      env     = local.db_env
      secrets = {}
      role    = aws_iam_role.crawl.arn
      ports   = []
      stop    = 30
    }
    migrate = {
      cpu     = 256
      memory  = 512
      command = ["python", "-m", "app.migrate"]
      env     = local.migrate_env
      secrets = {}
      role    = aws_iam_role.migrate.arn
      ports   = []
      stop    = 30
    }
  }
}

resource "aws_ecs_cluster" "main" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_task_definition" "app" {
  for_each                 = local.tasks
  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.exec[each.key].arn
  task_role_arn            = each.value.role

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  container_definitions = jsonencode([{
    name         = each.key
    image        = local.image
    essential    = true
    command      = each.value.command
    portMappings = each.value.ports
    stopTimeout  = each.value.stop
    environment  = [for k, v in each.value.env : { name = k, value = v }]
    secrets      = [for k, v in each.value.secrets : { name = k, valueFrom = v }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app[each.key].name
        awslogs-region        = var.region
        awslogs-stream-prefix = each.key
      }
    }
  }])
}

resource "aws_ecs_service" "web" {
  name                              = "web"
  cluster                           = aws_ecs_cluster.main.id
  task_definition                   = aws_ecs_task_definition.app["web"].arn
  desired_count                     = var.web_desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 120

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app["worker"].arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

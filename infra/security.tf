resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "Internal ALB"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "web" {
  name        = "${var.name}-web"
  description = "Web task: HTTPS out for the OIDC login"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "worker" {
  name        = "${var.name}-worker"
  description = "Worker task: HTTPS out through the NAT"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "crawl" {
  name        = "${var.name}-crawl"
  description = "Daily enqueue task: database and endpoints only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "rds" {
  name        = "${var.name}-rds"
  description = "PostgreSQL"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "endpoints" {
  name        = "${var.name}-endpoints"
  description = "VPC interface endpoints"
  vpc_id      = aws_vpc.main.id
}

# ---- ALB ----------------------------------------------------------------------------------------

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each          = toset(var.alb_allowed_cidrs)
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.key
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  for_each          = toset(var.alb_allowed_cidrs) # only to redirect to https
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.key
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_web" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.web.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

# ---- web: reachable from the ALB; endpoints, RDS, S3, and HTTPS out for the OIDC login ---------

resource "aws_vpc_security_group_ingress_rule" "web_from_alb" {
  security_group_id            = aws_security_group.web.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "web_to_endpoints" {
  security_group_id            = aws_security_group.web.id
  referenced_security_group_id = aws_security_group.endpoints.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "web_to_rds" {
  security_group_id            = aws_security_group.web.id
  referenced_security_group_id = aws_security_group.rds.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "web_to_s3" {
  security_group_id = aws_security_group.web.id
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# Decided at review (2026-10-07): login makes server-side calls to Entra ID (metadata, token, keys),
# which has no small stable address range. The web task never parses untrusted documents (the worker
# does), so HTTPS out via the NAT is the accepted trade-off over an allow-list proxy or Network Firewall.
resource "aws_vpc_security_group_egress_rule" "web_https" {
  security_group_id = aws_security_group.web.id
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# ---- worker: HTTPS anywhere (research, Graph, Confluence), RDS ---------------------------------

resource "aws_vpc_security_group_egress_rule" "worker_https" {
  security_group_id = aws_security_group.worker.id
  cidr_ipv4         = "0.0.0.0/0" # via the NAT; this also covers the interface endpoints
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "worker_to_rds" {
  security_group_id            = aws_security_group.worker.id
  referenced_security_group_id = aws_security_group.rds.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

# ---- RDS and endpoints -------------------------------------------------------------------------

resource "aws_vpc_security_group_ingress_rule" "rds_from_web" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = aws_security_group.web.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_worker" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = aws_security_group.worker.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_web" {
  security_group_id            = aws_security_group.endpoints.id
  referenced_security_group_id = aws_security_group.web.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "crawl_to_rds" {
  security_group_id            = aws_security_group.crawl.id
  referenced_security_group_id = aws_security_group.rds.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "crawl_to_endpoints" {
  security_group_id            = aws_security_group.crawl.id
  referenced_security_group_id = aws_security_group.endpoints.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_crawl" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = aws_security_group.crawl.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_crawl" {
  security_group_id            = aws_security_group.endpoints.id
  referenced_security_group_id = aws_security_group.crawl.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_worker" {
  security_group_id            = aws_security_group.endpoints.id
  referenced_security_group_id = aws_security_group.worker.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

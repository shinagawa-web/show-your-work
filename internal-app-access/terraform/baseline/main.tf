module "alb" {
  source          = "../modules/alb"
  name            = local.name
  internal        = false
  vpc_id          = module.base.vpc_id
  vpc_cidr        = module.base.vpc_cidr
  subnet_ids      = module.base.public_subnet_ids
  certificate_arn = module.base.certificate_arn
  apps            = local.apps
}

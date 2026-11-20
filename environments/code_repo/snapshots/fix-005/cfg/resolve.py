from .merge import merge


def resolve(defaults, file_config, env_config):
    return merge(env_config, merge(file_config, defaults))

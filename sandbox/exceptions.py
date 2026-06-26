class SandboxError(Exception):
    pass


class SandboxConfigError(SandboxError):
    pass


class SandboxValidationError(SandboxError):
    pass

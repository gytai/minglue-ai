class LoginException(Exception):
    """
    自定义登录异常LoginException
    """

    def __init__(self, data: str = None, message: str = None):
        # 把 message 交给基类保存：否则 str(exc) 永远是空串，
        # 审计日志里的 str(error) 会丢掉厂商/业务错误原因。
        super().__init__(message)
        self.data = data
        self.message = message


class AuthException(Exception):
    """
    自定义令牌异常AuthException
    """

    def __init__(self, data: str = None, message: str = None):
        # 把 message 交给基类保存：否则 str(exc) 永远是空串，
        # 审计日志里的 str(error) 会丢掉厂商/业务错误原因。
        super().__init__(message)
        self.data = data
        self.message = message


class PermissionException(Exception):
    """
    自定义权限异常PermissionException
    """

    def __init__(self, data: str = None, message: str = None):
        # 把 message 交给基类保存：否则 str(exc) 永远是空串，
        # 审计日志里的 str(error) 会丢掉厂商/业务错误原因。
        super().__init__(message)
        self.data = data
        self.message = message


class ServiceException(Exception):
    """
    自定义服务异常ServiceException
    """

    def __init__(self, data: str = None, message: str = None):
        # 把 message 交给基类保存：否则 str(exc) 永远是空串，
        # 审计日志里的 str(error) 会丢掉厂商/业务错误原因。
        super().__init__(message)
        self.data = data
        self.message = message


class ServiceWarning(Exception):
    """
    自定义服务警告ServiceWarning
    """

    def __init__(self, data: str = None, message: str = None):
        # 把 message 交给基类保存：否则 str(exc) 永远是空串，
        # 审计日志里的 str(error) 会丢掉厂商/业务错误原因。
        super().__init__(message)
        self.data = data
        self.message = message


class ModelValidatorException(Exception):
    """
    自定义模型校验异常ModelValidatorException
    """

    def __init__(self, data: str = None, message: str = None):
        # 把 message 交给基类保存：否则 str(exc) 永远是空串，
        # 审计日志里的 str(error) 会丢掉厂商/业务错误原因。
        super().__init__(message)
        self.data = data
        self.message = message

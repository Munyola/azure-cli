# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from knack.util import CLIError
from knack.log import get_logger
from knack.prompting import prompt_y_n, NoTTYException
from azure.cli.core.commands.parameters import get_resources_in_subscription

from ._constants import (
    REGISTRY_RESOURCE_TYPE,
    TASK_RESOURCE_ID_TEMPLATE,
    ACR_TASK_YAML_DEFAULT_NAME,
    get_classic_sku,
    get_managed_sku,
    get_premium_sku,
    get_valid_os,
    get_valid_architecture,
    get_valid_variant
)
from ._client_factory import cf_acr_registries

logger = get_logger(__name__)


def _arm_get_resource_by_name(cli_ctx, resource_name, resource_type):
    """Returns the ARM resource in the current subscription with resource_name.
    :param str resource_name: The name of resource
    :param str resource_type: The type of resource
    """
    result = get_resources_in_subscription(cli_ctx, resource_type)
    elements = [item for item in result if item.name.lower() ==
                resource_name.lower()]

    if not elements:
        from azure.cli.core._profile import Profile
        profile = Profile(cli_ctx=cli_ctx)
        message = "The resource with name '{}' and type '{}' could not be found".format(
            resource_name, resource_type)
        try:
            subscription = profile.get_subscription(
                cli_ctx.data['subscription_id'])
            raise ResourceNotFound(
                "{} in subscription '{} ({})'.".format(message, subscription['name'], subscription['id']))
        except (KeyError, TypeError) as e:
            logger.debug(
                "Could not get the current subscription. Exception: %s", str(e))
            raise ResourceNotFound(
                "{} in the current subscription.".format(message))

    elif len(elements) == 1:
        return elements[0]
    else:
        raise CLIError(
            "More than one resources with type '{}' are found with name '{}'.".format(
                resource_type, resource_name))


def _get_resource_group_name_by_resource_id(resource_id):
    """Returns the resource group name from parsing the resource id.
    :param str resource_id: The resource id
    """
    resource_id = resource_id.lower()
    resource_group_keyword = '/resourcegroups/'
    return resource_id[resource_id.index(resource_group_keyword) + len(
        resource_group_keyword): resource_id.index('/providers/')]


def get_resource_group_name_by_registry_name(cli_ctx, registry_name,
                                             resource_group_name=None):
    """Returns the resource group name for the container registry.
    :param str registry_name: The name of container registry
    :param str resource_group_name: The name of resource group
    """
    if not resource_group_name:
        arm_resource = _arm_get_resource_by_name(
            cli_ctx, registry_name, REGISTRY_RESOURCE_TYPE)
        resource_group_name = _get_resource_group_name_by_resource_id(
            arm_resource.id)
    return resource_group_name


def get_registry_by_name(cli_ctx, registry_name, resource_group_name=None):
    """Returns a tuple of Registry object and resource group name.
    :param str registry_name: The name of container registry
    :param str resource_group_name: The name of resource group
    """
    resource_group_name = get_resource_group_name_by_registry_name(
        cli_ctx, registry_name, resource_group_name)
    client = cf_acr_registries(cli_ctx)

    return client.get(resource_group_name, registry_name), resource_group_name


def get_registry_from_name_or_login_server(cli_ctx, login_server, registry_name=None):
    """Returns a Registry object for the specified name.
    :param str name: either the registry name or the login server of the registry.
    """
    client = cf_acr_registries(cli_ctx)
    registry_list = client.list()

    if registry_name:
        elements = [item for item in registry_list if
                    item.login_server.lower() == login_server.lower() or item.name.lower() == registry_name.lower()]
    else:
        elements = [item for item in registry_list if
                    item.login_server.lower() == login_server.lower()]

    if len(elements) == 1:
        return elements[0]
    if len(elements) > 1:
        logger.warning(
            "More than one registries were found by %s.", login_server)
    return None


def validate_managed_registry(cmd, registry_name, resource_group_name=None, message=None):
    """Raise CLIError if the registry in not in Managed SKU.
    :param str registry_name: The name of container registry
    :param str resource_group_name: The name of resource group
    """
    registry, resource_group_name = get_registry_by_name(
        cmd.cli_ctx, registry_name, resource_group_name)

    if not registry.sku or registry.sku.name not in get_managed_sku(cmd):
        raise CLIError(
            message or "This operation is only supported for managed registries.")

    return registry, resource_group_name


def validate_premium_registry(cmd, registry_name, resource_group_name=None, message=None):
    """Raise CLIError if the registry in not in Premium SKU.
    :param str registry_name: The name of container registry
    :param str resource_group_name: The name of resource group
    """
    registry, resource_group_name = get_registry_by_name(
        cmd.cli_ctx, registry_name, resource_group_name)

    if not registry.sku or registry.sku.name not in get_premium_sku(cmd):
        raise CLIError(
            message or "This operation is only supported for managed registries in Premium SKU.")

    return registry, resource_group_name


def validate_sku_update(cmd, current_sku, sku_parameter):
    """Validates a registry SKU update parameter.
    :param object sku_parameter: The registry SKU update parameter
    """
    if sku_parameter is None:
        return

    Sku = cmd.get_models('Sku')
    if isinstance(sku_parameter, dict):
        if 'name' not in sku_parameter:
            _invalid_sku_update(cmd)
        if sku_parameter['name'] not in get_classic_sku(cmd) and sku_parameter['name'] not in get_managed_sku(cmd):
            _invalid_sku_update(cmd)
        if current_sku in get_managed_sku(cmd) and sku_parameter['name'] in get_classic_sku(cmd):
            _invalid_sku_downgrade()
    elif isinstance(sku_parameter, Sku):
        if current_sku in get_managed_sku(cmd) and sku_parameter.name in get_classic_sku(cmd):
            _invalid_sku_downgrade()
    else:
        _invalid_sku_update(cmd)


def _invalid_sku_update(cmd):
    raise CLIError("Please specify SKU by '--sku SKU' or '--set sku.name=SKU'. Allowed SKUs: {0}".format(
        get_managed_sku(cmd)))


def _invalid_sku_downgrade():
    raise CLIError(
        "Managed registries could not be downgraded to Classic SKU.")


def user_confirmation(message, yes=False):
    if yes:
        return
    try:
        if not prompt_y_n(message):
            raise CLIError('Operation cancelled.')
    except NoTTYException:
        raise CLIError(
            'Unable to prompt for confirmation as no tty available. Use --yes.')


def get_validate_platform(cmd, platform):
    """Gets and validates the Platform from both flags
    :param str platform: The name of Platform passed by user in --platform flag
    """
    OS, Architecture = cmd.get_models('OS', 'Architecture')
    # Defaults
    platform_os = OS.linux.value
    platform_arch = Architecture.amd64.value
    platform_variant = None

    if platform:
        platform_split = platform.split('/')
        platform_os = platform_split[0]
        platform_arch = platform_split[1] if len(
            platform_split) > 1 else Architecture.amd64.value
        platform_variant = platform_split[2] if len(
            platform_split) > 2 else None

    platform_os = platform_os.lower()
    platform_arch = platform_arch.lower()

    valid_os = get_valid_os(cmd)
    valid_arch = get_valid_architecture(cmd)
    valid_variant = get_valid_variant(cmd)

    if platform_os not in valid_os:
        raise CLIError(
            "'{0}' is not a valid value for OS specified in --os or --platform. "
            "Valid options are {1}.".format(platform_os, ','.join(valid_os))
        )
    if platform_arch not in valid_arch:
        raise CLIError(
            "'{0}' is not a valid value for Architecture specified in --platform. "
            "Valid options are {1}.".format(
                platform_arch, ','.join(valid_arch))
        )
    if platform_variant and (platform_variant not in valid_variant):
        raise CLIError(
            "'{0}' is not a valid value for Variant specified in --platform. "
            "Valid options are {1}.".format(
                platform_variant, ','.join(valid_variant))
        )

    return platform_os, platform_arch, platform_variant


def get_yaml_template(cmd_value, timeout, file):
    """Generates yaml template
    :param str cmd_value: The command to execute in each step
    :param str timeout: The timeout for each step
    :param str file: The task definition
    """
    yaml_template = ""
    if cmd_value:
        yaml_template = "steps: \n  - cmd: {0}\n".format(cmd_value)
        if timeout:
            yaml_template += "    timeout: {0}\n".format(timeout)
    else:
        if not file:
            file = ACR_TASK_YAML_DEFAULT_NAME

        if file == "-":
            import sys
            for s in sys.stdin.readlines():
                yaml_template += s
        else:
            import os
            if os.path.exists(file):
                f = open(file, 'r')
                for line in f:
                    yaml_template += line
            else:
                raise CLIError("{0} does not exist.".format(file))

    if not yaml_template:
        raise CLIError("Failed to initialize yaml template.")

    return yaml_template


def get_custom_registry_credentials(cmd,
                                    auth_mode=None,
                                    login_server=None,
                                    username=None,
                                    password=None,
                                    identity=None,
                                    is_remove=False):
    """Get the credential object from the input
    :param str auth_mode: The login mode for the source registry
    :param str login_server: The login server of custom registry
    :param str username: The username for custom registry (plain text or a key vault secret URI)
    :param str password: The password for custom registry (plain text or a key vault secret URI)
    :param str identity: The task managed identity used for the credential
    """

    source_registry_credentials = None
    if auth_mode:
        SourceRegistryCredentials = cmd.get_models('SourceRegistryCredentials')
        source_registry_credentials = SourceRegistryCredentials(
            login_mode=auth_mode)

    custom_registries = None
    if login_server:
        # if null username and password (or identity), then remove the credential
        custom_reg_credential = None

        is_identity_credential = False
        if not username and not password:
            is_identity_credential = identity is not None

        CustomRegistryCredentials, SecretObject, SecretObjectType = cmd.get_models(
            'CustomRegistryCredentials',
            'SecretObject',
            'SecretObjectType'
        )

        if not is_remove:
            if is_identity_credential:
                custom_reg_credential = CustomRegistryCredentials(
                    identity=identity
                )
            else:
                custom_reg_credential = CustomRegistryCredentials(
                    user_name=SecretObject(
                        type=SecretObjectType.vaultsecret if is_vault_secret(
                            cmd, username)else SecretObjectType.opaque,
                        value=username
                    ),
                    password=SecretObject(
                        type=SecretObjectType.vaultsecret if is_vault_secret(
                            cmd, password) else SecretObjectType.opaque,
                        value=password
                    ),
                    identity=identity
                )

        custom_registries = {login_server: custom_reg_credential}

    Credentials = cmd.get_models('Credentials')
    return Credentials(
        source_registry=source_registry_credentials,
        custom_registries=custom_registries
    )


def build_timers_info(cmd, schedules):
    TimerTrigger, TriggerStatus = cmd.get_models(
        'TimerTrigger', 'TriggerStatus')
    timer_triggers = []

    # Provide a default name for the timer if no name was provided.
    for index, schedule in enumerate(schedules, start=1):
        split_schedule = None
        if ':' in schedule:
            split_schedule = schedule.split(":")
        timer_triggers.append(
            TimerTrigger(
                name=(split_schedule[0] if split_schedule else "t" + str(index)).strip(),
                status=TriggerStatus.enabled.value,
                schedule=split_schedule[1] if split_schedule else schedule
            ))
    return timer_triggers


def remove_timer_trigger(task_name,
                         timer_name,
                         timer_triggers):
    """Remove the timer trigger from the list of existing timer triggers for a task.
    :param str task_name: The name of the task
    :param str timer_name: The name of the timer trigger to be removed
    :param str timer_triggers: The list of existing timer_triggers for a task
    """

    if not timer_triggers:
        raise CLIError("No timer triggers exist for the task '{}'.".format(task_name))

    # Check that the timer trigger exists in the list and if not exit
    if any(timer.name == timer_name for timer in timer_triggers):
        for timer in timer_triggers:
            if timer.name == timer_name:
                timer_triggers.remove(timer)
    else:
        raise CLIError("The timer '{}' does not exist for the task '{}'.".format(timer_name, task_name))

    return timer_triggers


def is_vault_secret(cmd, credential):
    keyvault_dns = None
    try:
        keyvault_dns = cmd.cli_ctx.cloud.suffixes.keyvault_dns
    except ResourceNotFound:
        return False
    return keyvault_dns.upper() in credential.upper()


def get_task_id_from_task_name(cli_ctx, resource_group, registry_name, task_name):
    from azure.cli.core.commands.client_factory import get_subscription_id
    subscription_id = get_subscription_id(cli_ctx)
    return TASK_RESOURCE_ID_TEMPLATE.format(
        sub_id=subscription_id,
        rg=resource_group,
        reg=registry_name,
        name=task_name
    )


class ResourceNotFound(CLIError):
    """For exceptions that a resource couldn't be found in user's subscription
    """

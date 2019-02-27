"""Console script for pythx."""
import json
import logging
import os
import sys
import tempfile
import time
from os import environ, path
from pprint import pprint
from collections import defaultdict

import click
from tabulate import tabulate

from pythx.api import Client

if environ.get("PYTHX_DEBUG") is not None:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.ERROR)
LOGGER = logging.getLogger("pythx-cli")
DEFAULT_STORAGE_PATH = path.join(tempfile.gettempdir(), ".pythx.json")
CONFIG_KEYS = ("access", "refresh", "username", "password")

staging_opt = click.option(
    "--staging",
    default=False,
    is_flag=True,
    envvar="PYTHX_STAGING",
    help="Use the MythX staging environment",
)
config_opt = click.option(
    "--config",
    default=DEFAULT_STORAGE_PATH,
    envvar="PYTHX_CONFIG",
    help="Path to user credentials JSON file",
)


def parse_config(config_path, tokens_required=False):
    with open(config_path, "r") as config_f:
        config = json.load(config_f)
    keys_present = all(k in config for k in CONFIG_KEYS)
    if not (type(config) == dict and keys_present):
        click.echo(
            "Malformed config file at {} doesn't contain required keys {}".format(
                config_path, CONFIG_KEYS
            )
        )
        sys.exit(1)
    if tokens_required and not (config["access"] and config["refresh"]):
        click.echo(
            "Malformed config file at {} does not contain access and refresh token".format(
                config_path
            )
        )
        sys.exit(1)
    return config


def update_config(config_path, username, password, access, refresh):
    with open(config_path, "w+") as config_f:
        json.dump(
            {
                "username": username,
                "password": password,
                "access": access,
                "refresh": refresh,
            },
            config_f,
        )


def recover_client(config_path, staging=False, exit_on_missing=False):
    if not path.isfile(config_path):
        if exit_on_missing:
            return None
        # config doesn't exist - assume first use
        eth_address = environ.get("PYTHX_USERNAME") or click.prompt(
            "Please enter your Ethereum address",
            type=click.STRING,
            default="0x0000000000000000000000000000000000000000",
        )
        password = environ.get("PYTHX_PASSWORD") or click.prompt(
            "Please enter your MythX password",
            type=click.STRING,
            hide_input=True,
            default="trial",
        )
        c = Client(eth_address=eth_address, password=password, staging=staging)
        c.login()
        update_config(
            config_path=config_path,
            username=eth_address,
            password=password,
            access=c.access_token,
            refresh=c.refresh_token,
        )
    else:
        config = parse_config(config_path, tokens_required=True)
        c = Client(
            eth_address=config["username"],
            password=config["password"],
            access_token=config["access"],
            refresh_token=config["refresh"],
            staging=staging,
        )
    return c


@click.group()
def cli():
    pass  # pragma: no cover


@cli.command(help="Login to your MythX account")
@staging_opt
@config_opt
def login(staging, config):
    c = recover_client(config, staging)
    login_resp = c.login()
    LOGGER.debug(
        "Access token %s\nRefresh token: %s",
        login_resp.access_token,
        login_resp.refresh_token,
    )
    click.echo("Successfully logged in as {}".format(c.eth_address))
    update_config(
        config_path=config,
        username=c.eth_address,
        password=c.password,
        access=c.access_token,
        refresh=c.refresh_token,
    )


@cli.command(help="Log out of your MythX account")
@config_opt
@staging_opt
def logout(config, staging):
    c = recover_client(config_path=config, staging=staging, exit_on_missing=True)
    if c is None:
        click.echo("You are already logged out.")
        sys.exit(0)
    # delete the credentials storage and logout
    os.remove(config)
    c.logout()
    click.echo("Successfully logged out")


@cli.command(help="Refresh your MythX API token")
@staging_opt
@config_opt
def refresh(staging, config):
    c = recover_client(config, staging)
    login_resp = c.refresh()
    LOGGER.debug(
        "Access token %s\nRefresh token: %s",
        login_resp.access_token,
        login_resp.refresh_token,
    )
    click.echo("Successfully refreshed tokens for {}".format(c.eth_address))
    update_config(
        config_path=config,
        username=c.eth_address,
        password=c.password,
        access=c.access_token,
        refresh=c.refresh_token,
    )


@cli.command(help="Get the OpenAPI spec in HTML or YAML format")
@staging_opt
@click.option("--html", "mode", flag_value="html", help="Get the HTML OpenAPI spec")
@click.option(
    "--yaml", "mode", flag_value="yaml", default=True, help="Get the YAML OpenAPI spec"
)
def openapi(staging, mode):
    c = Client()  # no auth required
    click.echo(c.openapi(mode).data)


@cli.command(help="Print version information of PythX and the API")
@staging_opt
def version(staging):
    c = Client()  # no auth required
    resp = c.version().to_dict()
    data = ((k.title(), v) for k, v in resp.items())
    click.echo(tabulate(data, tablefmt="fancy_grid"))


@cli.command(help="Get the status of an analysis by its UUID")
@config_opt
@staging_opt
@click.argument("uuid", type=click.UUID)
def status(config, staging, uuid):
    c = recover_client(config_path=config, staging=staging)
    resp = c.status(uuid).analysis.to_dict()
    data = ((k, v) for k, v in resp.items())
    click.echo(tabulate(data, tablefmt="fancy_grid"))
    update_config(
        config_path=config,
        username=c.eth_address,
        password=c.password,
        access=c.access_token,
        refresh=c.refresh_token,
    )


@cli.command(help="Get a greppable overview of submitted analyses")
@config_opt
@staging_opt
@click.option(
    "--number",
    default=20,
    type=click.IntRange(min=1, max=100),
    help="The number of most recent analysis jobs to display",
)
def ps(config, staging, number):
    resp = ps_core(config, staging, number)
    data = [(a.uuid, a.status, a.submitted_at) for a in resp.analyses]
    click.echo(tabulate(data, tablefmt="fancy_grid"))


def ps_core(config, staging, number):
    c = recover_client(config_path=config, staging=staging)
    if c.eth_address == "0x0000000000000000000000000000000000000000":
        click.echo(
            (
                "This functionality is only available to registered users. "
                "Head over to https://mythx.io/ and register a free account to "
                "list your past analyses. Alternatively, you can look up the "
                "status of a specific job by calling 'pythx status <uuid>'."
            )
        )
        sys.exit(0)
    resp = c.analysis_list()
    # todo: pagination if too few
    resp.analyses = resp.analyses[: number + 1]
    update_config(
        config_path=config,
        username=c.eth_address,
        password=c.password,
        access=c.access_token,
        refresh=c.refresh_token,
    )
    return resp


@cli.command(help="Display the most recent analysis jobs and their status")
@config_opt
@staging_opt
@click.option("--interval", default=5, type=click.INT, help="Refresh interval")
def top(config, staging, interval):
    while True:
        resp = ps_core(config, staging, 20)
        click.clear()
        data = [(a.uuid, a.status, a.submitted_at) for a in resp.analyses]
        click.echo(tabulate(data, tablefmt="fancy_grid"))
        time.sleep(interval)


@cli.command(help="Submit a new analysis job based on source code, byte code, or both")
@config_opt
@staging_opt
@click.option(
    "--bytecode",
    "-b",
    type=click.STRING,
    default=None,
    help="Analysis job creation byte code",
)
@click.option(
    "--source",
    "-s",
    type=click.STRING,
    default=None,
    help="Analysis job Solidity source code",
)
@click.option(
    "--bytecode-file",
    "-bf",
    type=click.Path(exists=True),
    default=None,
    help="Path to file containing creation bytecode",
)
@click.option(
    "--source-file",
    "-sf",
    type=click.Path(exists=True),
    default=None,
    help="Path to file containing Solidity source code",
)
def check(config, staging, bytecode, source, bytecode_file, source_file):
    c = recover_client(config_path=config, staging=staging)
    bytecode_f = bytecode if bytecode else None
    sources_f = {"cli-src.sol": {"source": source}} if source else {}
    if bytecode_file:
        with open(bytecode_file, "r") as bf:
            bytecode_f = bf.read().strip()  # CLI arg bytecode takes preference
    if source_file:
        with open(source_file, "r") as sf:
            sources_f = {path.abspath(source_file): {"source": sf.read().strip()}}

    resp = c.analyze(bytecode=bytecode_f, sources=sources_f)
    click.echo("Analysis submitted as job {}".format(resp.analysis.uuid))
    update_config(
        config_path=config,
        username=c.eth_address,
        password=c.password,
        access=c.access_token,
        refresh=c.refresh_token,
    )


def get_source_location_by_offset(filename, offset):
    overall = 0
    line_ctr = 0
    with open(filename) as f:
        for line in f:
            line_ctr += 1
            overall += len(line)
            if overall >= offset:
                return line_ctr, overall - offset
    LOGGER.error(
        "Error finding the source location in {} for offset {}".format(filename, offset)
    )
    sys.exit(1)


@cli.command(help="Check the detected issues of a finished analysis job")
@config_opt
@staging_opt
@click.argument("uuid", type=click.STRING)
def report(config, staging, uuid):
    c = recover_client(config_path=config, staging=staging)
    resp = c.report(uuid)

    file_to_issue = defaultdict(list)

    for issue in resp.issues:
        source_locs = [loc.source_map.split(":") for loc in issue.locations]
        for offset, length, file_idx in source_locs:
            if resp.source_list:
                filename = resp.source_list[int(file_idx)]
                line, column = get_source_location_by_offset(filename, int(offset))
            else:
                filename = "Unknown"
        file_to_issue[filename].append(
            (line, column, issue.swc_title, issue.severity, issue.description_short)
        )

    for filename, data in file_to_issue.items():
        click.echo("Report for {}".format(filename))
        click.echo(
            tabulate(
                data,
                tablefmt="fancy_grid",
                headers=(
                    "Line",
                    "Column",
                    "SWC Title",
                    "Severity",
                    "Short Description",
                ),
            )
        )

    update_config(
        config_path=config,
        username=c.eth_address,
        password=c.password,
        access=c.access_token,
        refresh=c.refresh_token,
    )

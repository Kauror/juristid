"""Properties of the management commands themselves.

No database and no execution: these ask what the commands *are*, which is
enough to catch the class of mistake that only appears when somebody runs one.

`rebuild_document_derivatives` had `--version`. Every Django management command
already defines that option, and argparse raises the conflict when the parser is
built at call time rather than at import — so the command imported cleanly,
passed every test that did not invoke it, and failed the first time an operator
typed it on a live server.
"""

from __future__ import annotations

import pytest
from django.core.management import get_commands, load_command_class

#: Commands this project owns. Django's own and third-party ones are somebody
#: else's problem, and their options are allowed to be whatever they are.
OUR_COMMANDS = sorted(name for name, app in get_commands().items() if app.startswith("app."))


def test_the_project_actually_has_commands() -> None:
    """Guards the guard: an empty list would make every test below vacuous."""
    assert len(OUR_COMMANDS) > 5


@pytest.mark.parametrize("name", OUR_COMMANDS)
def test_a_commands_parser_builds(name: str) -> None:
    """Building the parser is where an option collision raises.

    Which means importing the module proves nothing, and this is the cheapest
    possible thing that does.
    """
    command = load_command_class(get_commands()[name], name)
    parser = command.create_parser("manage.py", name)

    assert parser.prog.endswith(name)


@pytest.mark.parametrize("name", OUR_COMMANDS)
def test_a_command_does_not_shadow_a_django_option(name: str) -> None:
    """`--version`, `--verbosity` and friends belong to the base command.

    Redefining one is not a style question: argparse refuses, and the command
    becomes unrunnable.
    """
    command = load_command_class(get_commands()[name], name)
    parser = command.create_parser("manage.py", name)

    options = [option for action in parser._actions for option in action.option_strings]
    assert len(options) == len(set(options)), sorted(options)


@pytest.mark.parametrize("name", OUR_COMMANDS)
def test_a_command_says_what_it_is_for(name: str) -> None:
    """An operator reading `manage.py help` should not have to open the source."""
    command = load_command_class(get_commands()[name], name)
    assert command.help, name

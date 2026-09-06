"""
/prompt command handler - displays the current system prompt.
"""

from .base import CmdHandler
from .registry import register_command


class PromptCmdHandler(CmdHandler):
    """Command handler for /prompt command."""

    @property
    def name(self) -> str:
        return "/prompt"

    @property
    def description(self) -> str:
        return "Show the current system prompt"

    def handle(self, shell, user_input: str) -> bool:
        """Handle the /prompt command."""
        if user_input.lower() == self.name.lower():
            self._print_prompt(shell)
            return True
        return False

    def _print_prompt(self, shell) -> None:
        """Print the current system prompt."""
        from rich.console import Console
        from rich.table import Table

        from janito.system_labels import LABEL_CLI
        from janito.system_prompt import SECTION_SKILLS, default_system_prompt_manager

        # Get the actual system prompt from the shell
        effective_prompt = shell.get_system_prompt()

        if effective_prompt is None:
            Console(markup=False).print("No system prompt is active (--no-system-prompt)")
            return

        # The config-aware default: the skills/agents.md sections plus the
        # configured start section (system-prompt / system-prompt-file), when
        # set.  The shell prompt was resolved through the same
        # SessionSetup.effective_system_prompt() path, so a config-provided
        # start is classified as "default" here (keeping the section table)
        # instead of drifting into the plain custom-prompt view.
        manager = default_system_prompt_manager()
        if effective_prompt == manager.render():
            # Default prompt: show each section as a rich table row with its
            # name, line count and content.  Only advertise skills in the
            # title when a "skills" section is actually present (skills
            # enabled and at least one skill advertised).
            sections = list(manager.get_all_sections())
            has_skills = any(section.name == SECTION_SKILLS for section in sections)
            title = "System Prompt - Default (with Skills)" if has_skills else "System Prompt - Default"
            table = Table(
                title=title,
                title_style="bold",
                header_style="bold cyan",
            )
            table.add_column("Section", style="green", no_wrap=True)
            table.add_column("Lines", justify="right")
            table.add_column("Content", overflow="fold")
            for section in sections:
                body = section.text.rstrip()
                line_count = len(body.splitlines()) if body else 0
                # Show the section's label when set (e.g. "built-in" or
                # "(config) ~/base.md"), falling back to the section name
                # (issue #86).
                table.add_row(section.label or section.name, str(line_count), body)
                # Empty row after each section for visual context split.
                table.add_row("", "", "")
            Console(markup=False).print(table)
            return

        # Custom prompt (-S): show it as a single section labeled "-S" (issue
        # #86), matching the default prompt's Section/Lines/Content layout.
        table = Table(
            title="System Prompt",
            title_style="bold",
            header_style="bold cyan",
        )
        table.add_column("Section", style="green", no_wrap=True)
        table.add_column("Lines", justify="right")
        table.add_column("Content", overflow="fold")
        body = effective_prompt.rstrip()
        line_count = len(body.splitlines()) if body else 0
        table.add_row(LABEL_CLI, str(line_count), body)
        Console(markup=False).print(table)


# Register this handler
_handler = PromptCmdHandler()
register_command(_handler)

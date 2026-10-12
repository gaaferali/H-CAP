"""Django command discovery adapter for the external AI/automation source."""

from automation.ai_source import import_data_command_class


Command = import_data_command_class()

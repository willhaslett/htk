"""Shared CLI presentation; parsing and command behavior remain with each tool."""

import argparse
import sys


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_help(sys.stderr)
        self.exit(2, f"\n{self.prog}: error: {message}\n")

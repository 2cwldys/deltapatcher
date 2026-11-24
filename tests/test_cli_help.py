"""The command line's help in colour, and its window: kept open when it is a window of its own on Windows
(double-clicked, a shortcut), closed at once with --close, never held open in a terminal or a pipe."""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402,F401  (first: settings and logs go to a sandbox)
from deltapatcher import cli, color  # noqa: E402

VERSION_LINE = "%s %s\n" % (cli.APP_NAME, cli.__version__)
CODE = "\x1b[" + color.STYLES["code"] + "m%s\x1b[0m"          # painted as something to copy


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def own_window(on=True):
    return mock.patch.object(cli, "window_of_its_own", return_value=on)


class Help(unittest.TestCase):
    def tearDown(self):
        color._on.clear()

    def test_colour_keeps_the_layout(self):
        plain_help = cli.build_parser().format_help()
        with mock.patch.dict(color._on, {"out": True}):
            painted = cli.colorize_help(plain_help)
        self.assertIn("\x1b[", painted)
        self.assertEqual(color.plain(painted), plain_help)          # only colour added: every column where it was
        lines = {color.plain(line): line for line in painted.splitlines()}
        options = next(line for p, line in lines.items() if p == "options:")
        self.assertTrue(options.startswith("\x1b[1;97m"))           # a heading: bold white
        publish = next(line for p, line in lines.items() if p.lstrip().startswith("publish "))
        self.assertIn(CODE % "publish", publish)                    # a command: the app's blue
        example = next(line for p, line in lines.items() if p.lstrip().startswith(cli.prog_name() + " release --to local"))
        self.assertIn(CODE % (cli.prog_name() + " release --to local"), example)  # the command, not its (comment)

    def test_every_example_is_a_real_command(self):
        import shlex
        prog = cli.prog_name()
        examples = [line.strip() for line in cli.build_parser().format_help().splitlines()
                    if line.startswith("  " + prog + " ")]
        self.assertGreaterEqual(len(examples), 10)
        self.assertTrue(any(" release --version" in e for e in examples))           # the VPS route is shown
        self.assertTrue(any("--source https://" in e for e in examples))
        for line in examples:
            command = line.split("  (")[0]                                            # without its (comment)
            argv = shlex.split(command)[1:]
            try:
                cli.build_parser().parse_args(argv)
            except SystemExit:
                self.fail("the help's example does not parse: %s" % command)

    def test_subcommand_help_too(self):
        with mock.patch.dict(color._on, {"out": True}):
            painted = cli.build_parser()._subparsers._group_actions[0].choices["update"].format_help()
        self.assertIn(CODE % "--source SOURCE", painted)
        self.assertIn(CODE % "install", painted)

    def test_no_colour_when_asked(self):
        rc, text, _ = run(["--no-color"])
        self.assertEqual(rc, 0)
        self.assertNotIn("\x1b[", text)


class Window(unittest.TestCase):
    def test_in_a_terminal_nothing_waits(self):
        with own_window(False), mock.patch("builtins.input", side_effect=AssertionError("waited in a terminal")):
            self.assertEqual(run([])[0], 0)
            self.assertEqual(run(["--no-log", "version"])[0], 0)

    def test_a_window_of_its_own_waits_after_a_command(self):
        with own_window(), mock.patch("builtins.input", return_value="") as enter:
            rc, text, _ = run(["--no-log", "version"])
        self.assertEqual(rc, 0)
        self.assertIn(VERSION_LINE, text)
        enter.assert_called_once()
        self.assertIn("Press Enter to close", enter.call_args[0][0])

    def test_close_never_waits(self):
        with own_window(), mock.patch("builtins.input", side_effect=AssertionError("waited with --close")):
            rc, text, _ = run(["--close", "--no-log", "version"])
            self.assertEqual(rc, 0)
            self.assertIn(VERSION_LINE, text)
            rc, text, _ = run(["--close"])                       # no command: the help, and no prompt
            self.assertEqual(rc, 0)
            self.assertIn("usage:", text)

    def test_a_mistake_waits_to_be_read(self):
        with own_window(), mock.patch("builtins.input", return_value="") as enter:
            with self.assertRaises(SystemExit):
                run(["nonsense"])
        enter.assert_called_once()

    def test_json_never_waits(self):
        with own_window(), mock.patch("builtins.input", side_effect=AssertionError("waited in --json")):
            rc, text, _ = run(["--json", "--no-log", "version"])
        self.assertEqual(rc, 0)

    def test_double_clicked_opens_the_menu_and_its_prompt(self):
        # the menu's 5 is the prompt; exit there goes back to the menu, and 0 closes it
        typed = ["5", "", "version", cli.prog_name() + " version", "help update", "nonsense", "--json version", "exit",
                 "0", "version"]
        with own_window(), mock.patch("builtins.input", side_effect=typed) as keys:
            rc, text, err = run(["--no-log"])
        self.assertEqual(rc, 0)
        self.assertEqual(keys.call_count, 9)                       # 0 closed it: the last line was never read
        self.assertIn("What do you want to do?", text)
        self.assertIn("Type a command", text)
        self.assertIn("exit goes back to the menu", text)
        # the two version commands (one with the program's name first), and the menu's heading, shown twice
        self.assertEqual(text.count(VERSION_LINE), 4)
        self.assertIn("--source SOURCE", text)                      # help update
        self.assertIn("invalid choice: 'nonsense'", err)           # explained, and the prompt carried on
        self.assertIn("--json is for programs", text)

    def test_the_prompt_ends_at_end_of_input(self):
        with own_window(), mock.patch("builtins.input", side_effect=EOFError):
            self.assertEqual(run([])[0], 0)

    def test_split_line_keeps_quoted_paths(self):
        self.assertEqual(cli.split_line('update "D:\\My Game" --source https://example.com/game/'),
                         ["update", "D:\\My Game", "--source", "https://example.com/game/"])

    def test_a_pipe_is_not_a_window_of_its_own(self):
        # the tests' output is captured or piped: never "double-clicked"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(cli.window_of_its_own())

    def test_a_terminal_is_not_its_own_console(self):
        with mock.patch.dict(os.environ, {"DELTAPATCHER_WINDOW": ""}):
            self.assertFalse(cli._own_console())


if __name__ == "__main__":
    unittest.main()

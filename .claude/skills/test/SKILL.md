---
name: test
description: Run the space-ground-sim test suite and fix any failures
---
Run `py -m unittest discover -s tests -t . -v` from the project root.
If any test fails, find the root cause, fix the code (not the test, unless the test is wrong), and re-run until all tests pass.
Finish by running `py run_demo.py --seconds 5` to confirm the elements still talk to each other.

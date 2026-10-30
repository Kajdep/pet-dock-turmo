# SPDX-License-Identifier: GPL-3.0-or-later
#
# Derived from turing-smart-screen-python's library/log.py.
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine)
#
# This file is part of a GPL-3.0-or-later vendored subset. See ../LICENSE.
"""Provide the logger expected by the vendored driver without configuring an app logger."""

import logging

logger = logging.getLogger("codex_pet_turmo.vendor.turing")
logger.addHandler(logging.NullHandler())

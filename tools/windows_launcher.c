/*
 * Pet Dock Windows launcher
 * Copyright (C) 2026 Pet Dock contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * This program is free software: you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the Free
 * Software Foundation, either version 3 of the License, or (at your option)
 * any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
 * more details.
 */

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <stdlib.h>
#include <wchar.h>

#define APP_TITLE L"Pet Dock"

static void show_message(const wchar_t *message) {
    MessageBoxW(NULL, message, APP_TITLE, MB_OK | MB_ICONERROR);
}

static wchar_t *copy_module_directory(void) {
    DWORD size = MAX_PATH;

    for (;;) {
        wchar_t *candidate = (wchar_t *)malloc((size_t)size * sizeof(*candidate));
        DWORD length;
        wchar_t *last_separator;

        if (candidate == NULL) {
            show_message(L"Pet Dock could not allocate memory for its startup path.");
            return NULL;
        }

        length = GetModuleFileNameW(NULL, candidate, size);
        if (length == 0) {
            free(candidate);
            show_message(L"Pet Dock could not determine its installation folder.");
            return NULL;
        }
        if (length < size - 1) {
            last_separator = wcsrchr(candidate, L'\\');
            if (last_separator == NULL) {
                free(candidate);
                show_message(L"Pet Dock could not determine its installation folder.");
                return NULL;
            }
            *last_separator = L'\0';
            return candidate;
        }

        free(candidate);
        if (size >= 32768) {
            show_message(L"Pet Dock's installation path is too long to start.");
            return NULL;
        }
        size *= 2;
    }
}

static wchar_t *join_path(const wchar_t *directory, const wchar_t *relative_path) {
    size_t directory_length = wcslen(directory);
    size_t relative_length = wcslen(relative_path);
    size_t total = directory_length + 1 + relative_length + 1;
    wchar_t *result = (wchar_t *)malloc(total * sizeof(*result));

    if (result == NULL) {
        return NULL;
    }
    swprintf(result, total, L"%ls\\%ls", directory, relative_path);
    return result;
}

static wchar_t *format_process_error(DWORD error_code, const wchar_t *python_path) {
    wchar_t *system_message = NULL;
    wchar_t *message;
    DWORD length = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
            FORMAT_MESSAGE_IGNORE_INSERTS,
        NULL,
        error_code,
        0,
        (wchar_t *)&system_message,
        0,
        NULL);
    const wchar_t *detail = (length != 0 && system_message != NULL)
        ? system_message
        : L"No further Windows error description is available.";
    size_t needed = wcslen(L"Pet Dock could not start its bundled Python runtime.\n\n") +
                    wcslen(python_path) +
                    wcslen(L"\n\nWindows reported error ") + 24 + wcslen(detail) + 1;

    message = (wchar_t *)malloc(needed * sizeof(*message));
    if (message != NULL) {
        swprintf(
            message,
            needed,
            L"Pet Dock could not start its bundled Python runtime.\n\n%ls\n\n"
            L"Windows reported error %lu:\n%ls",
            python_path,
            (unsigned long)error_code,
            detail);
    }
    if (system_message != NULL) {
        LocalFree(system_message);
    }
    return message;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous_instance,
                    PWSTR command_line, int show_command) {
    wchar_t *root = NULL;
    wchar_t *python = NULL;
    wchar_t *bootstrap = NULL;
    wchar_t *command = NULL;
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    BOOL created;
    DWORD error_code;
    size_t command_size;

    (void)instance;
    (void)previous_instance;
    (void)command_line;
    (void)show_command;

    root = copy_module_directory();
    if (root == NULL) {
        return 1;
    }
    python = join_path(root, L"runtime\\pythonw.exe");
    bootstrap = join_path(root, L"app\\bootstrap.py");
    if (python == NULL || bootstrap == NULL) {
        show_message(L"Pet Dock could not allocate memory for its startup command.");
        free(bootstrap);
        free(python);
        free(root);
        return 1;
    }

    if (GetFileAttributesW(python) == INVALID_FILE_ATTRIBUTES) {
        show_message(
            L"Pet Dock cannot start because a required bundled file is missing.\n\n"
            L"Expected runtime\\pythonw.exe beside PetDock.exe.\n\n"
            L"Please reinstall Pet Dock or use a complete portable package.");
        free(bootstrap);
        free(python);
        free(root);
        return 1;
    }
    if (GetFileAttributesW(bootstrap) == INVALID_FILE_ATTRIBUTES) {
        show_message(
            L"Pet Dock cannot start because a required bundled file is missing.\n\n"
            L"Expected app\\bootstrap.py beside PetDock.exe.\n\n"
            L"Please reinstall Pet Dock or use a complete portable package.");
        free(bootstrap);
        free(python);
        free(root);
        return 1;
    }

    /* Paths are absolute and quoted; CreateProcessW may modify command in place. */
    command_size = wcslen(python) + wcslen(bootstrap) + 6;
    command = (wchar_t *)malloc(command_size * sizeof(*command));
    if (command == NULL) {
        show_message(L"Pet Dock could not allocate memory for its startup command.");
        free(bootstrap);
        free(python);
        free(root);
        return 1;
    }
    swprintf(command, command_size, L"\"%ls\" \"%ls\"", python, bootstrap);

    ZeroMemory(&startup, sizeof(startup));
    ZeroMemory(&process, sizeof(process));
    startup.cb = sizeof(startup);
    created = CreateProcessW(
        python,
        command,
        NULL,
        NULL,
        FALSE,
        0,
        NULL,
        root,
        &startup,
        &process);
    if (!created) {
        wchar_t *message;
        error_code = GetLastError();
        message = format_process_error(error_code, python);
        show_message(message != NULL
            ? message
            : L"Pet Dock could not start its bundled Python runtime. Please reinstall Pet Dock.");
        free(message);
        free(command);
        free(bootstrap);
        free(python);
        free(root);
        return 1;
    }

    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    free(command);
    free(bootstrap);
    free(python);
    free(root);
    return 0;
}

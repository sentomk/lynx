# Copyright 2026 The Lynx Authors. All rights reserved.
# Licensed under the Apache License Version 2.0 that can be found in the
# LICENSE file in the root directory of this source tree.

cmake_minimum_required(VERSION 3.19)
project(lynx_cdpc_generation_test LANGUAGES CXX)

set(CDPC_SOURCE_DIR "" CACHE PATH "Checkout matching protocol/cdpc_toolchain.json")
set(CDPC_CONTRACT_DIR "" CACHE PATH "Directory emitted by tools/cdpc runtime write")
get_filename_component(lynx_root "${CMAKE_CURRENT_LIST_DIR}/../../../../.." ABSOLUTE)
file(READ "${CMAKE_CURRENT_LIST_DIR}/../../cdpc_toolchain.json" pin)
string(JSON revision GET "${pin}" revision)
execute_process(COMMAND git -C "${CDPC_SOURCE_DIR}" rev-parse HEAD
  OUTPUT_VARIABLE actual_revision OUTPUT_STRIP_TRAILING_WHITESPACE RESULT_VARIABLE git_result)
if(NOT git_result EQUAL 0 OR NOT actual_revision STREQUAL revision)
  message(FATAL_ERROR "CDPC_SOURCE_DIR must match the pinned revision ${revision}")
endif()
execute_process(COMMAND git -C "${CDPC_SOURCE_DIR}" status --porcelain --untracked-files=all
  -- python/cdpc_core pyproject.toml include src
  OUTPUT_VARIABLE dirty_sources OUTPUT_STRIP_TRAILING_WHITESPACE RESULT_VARIABLE status_result)
if(NOT status_result EQUAL 0 OR NOT dirty_sources STREQUAL "")
  message(FATAL_ERROR "Pinned CDPC tool/runtime sources must be clean")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${CMAKE_CURRENT_LIST_DIR}/../../cdpc_toolchain.json")
if(NOT EXISTS "${CDPC_CONTRACT_DIR}/cdpc_contracts.generated.h")
  message(FATAL_ERROR "Generate a catalog with tools/cdpc runtime write --output-directory first")
endif()
set(CDPC_LYNX_SOURCE_DIR "${lynx_root}" CACHE PATH "" FORCE)
set(CDPC_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(CDPC_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
add_subdirectory("${CDPC_SOURCE_DIR}" cdpc)
add_executable(lynx_cdpc_generation_test catalog_test.cc second_translation_unit.cc)
target_include_directories(lynx_cdpc_generation_test PRIVATE "${CDPC_CONTRACT_DIR}")
target_link_libraries(lynx_cdpc_generation_test PRIVATE cdpc::runtime)
enable_testing()
add_test(NAME generated_catalog COMMAND lynx_cdpc_generation_test)
set_tests_properties(generated_catalog PROPERTIES TIMEOUT 30)

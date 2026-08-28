cmake_minimum_required(VERSION 3.16)
project(UnrealShimCheck LANGUAGES CXX)

# Compile-checks the Unreal gameplay module without Unreal.
#
# The module sources under ../../Source/SygnifGame are compiled in place against
# the minimal shim in shim/, with the same compiler settings an Unreal game
# module uses. This catches every mistake a compiler can catch. It does NOT
# exercise UnrealHeaderTool, so reflection, Blueprint exposure, replication and
# property serialisation are unverified -- see shim/CoreMinimal.h.

set(CMAKE_CXX_STANDARD 20)          # UE5 builds gameplay modules as C++20
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(UNREAL_MODULE_FLAGS -fno-exceptions -fno-rtti)
set(WARNING_FLAGS -Wall -Wextra -Werror -Wshadow)

set(REALENGINE_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../../..)

# CONFIGURE_DEPENDS makes the build re-run the glob when the directory changes.
# Without it CMake expands the glob once, at configure time, and a newly added
# source file is silently not compiled -- the build succeeds while proving
# nothing about the file you just wrote.
file(GLOB MODULE_SOURCES CONFIGURE_DEPENDS
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Source/SygnifGame/Private/*.cpp)

add_library(unreal_shim_check STATIC
    ${MODULE_SOURCES}
    ${REALENGINE_ROOT}/cpp/src/Sweep.cpp
    ${REALENGINE_ROOT}/cpp/src/TileGrid.cpp
)

target_include_directories(unreal_shim_check PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/shim                          # the fake engine
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Source/SygnifGame/Public # the module
    ${REALENGINE_ROOT}/cpp/include                             # the real core
)

target_compile_options(unreal_shim_check PRIVATE ${UNREAL_MODULE_FLAGS} ${WARNING_FLAGS})

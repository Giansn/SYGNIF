cmake_minimum_required(VERSION 3.16)
project(GameCore LANGUAGES CXX)

# C++17 is the safe floor. UE5 builds engine code as C++20, but a gameplay
# module is routinely compiled against older standards depending on the target,
# and nothing in this core needs anything newer.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ---------------------------------------------------------------------------
# The point of this configuration: prove the core drops into an Unreal module
# unchanged.
#
# Unreal game modules build with exceptions and RTTI disabled by default
# (bEnableExceptions = false, bUseRTTI = false in the module's Build.cs). Code
# that quietly depends on either compiles fine in a normal C++ project and then
# fails the moment it is pulled into the engine — usually as a wall of errors
# in someone else's header. Building with them off here means that mismatch
# surfaces on this machine in two seconds rather than after a 40-minute engine
# build.
#
# This is also why the tests use a hand-rolled harness instead of GoogleTest:
# a framework that expects exceptions would force them back on and the build
# would stop being representative.
# ---------------------------------------------------------------------------
set(UNREAL_MODULE_FLAGS -fno-exceptions -fno-rtti)

set(WARNING_FLAGS
    -Wall
    -Wextra
    -Wpedantic
    -Werror
    -Wshadow            # a local shadowing a member is a classic gameplay bug
    -Wconversion        # silent float/int narrowing wrecks fixed-point determinism
    -Wsign-conversion
    -Wfloat-equal       # flags == on floats, which is nearly always a mistake
    -Wold-style-cast
)

add_library(gamecore STATIC
    src/Sweep.cpp
    src/TileGrid.cpp
)
target_include_directories(gamecore PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_options(gamecore PRIVATE ${UNREAL_MODULE_FLAGS} ${WARNING_FLAGS})

enable_testing()

add_executable(gamecore_tests
    tests/TinyTest.cpp
    tests/CoreTests.cpp
)
target_link_libraries(gamecore_tests PRIVATE gamecore)
target_include_directories(gamecore_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/tests)
target_compile_options(gamecore_tests PRIVATE ${UNREAL_MODULE_FLAGS} ${WARNING_FLAGS})

add_test(NAME gamecore_tests COMMAND gamecore_tests)

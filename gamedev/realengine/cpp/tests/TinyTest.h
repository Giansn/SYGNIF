#pragma once

// A ~60 line test harness.
//
// GoogleTest is the obvious choice and it is deliberately not used here. Unreal
// game modules build with exceptions and RTTI disabled, and this whole core is
// compiled that way to prove it drops into a module unchanged. Pulling in a test
// framework that expects exceptions would either force them back on — making the
// build no longer representative — or need enough configuration that the
// dependency stops paying for itself. Failures are counted, not thrown.
//
// For an actual Unreal project the in-engine equivalent is the Automation
// Testing framework (IMPLEMENT_SIMPLE_AUTOMATION_TEST), which can run headless
// via UnrealEditor-Cmd -ExecCmds="Automation RunTests ...". That runs the engine;
// this runs in milliseconds, which is what you want for pure gameplay logic.

#include <cmath>
#include <cstdio>

namespace tinytest
{

using TestFn = void (*)();

void Register(const char* Name, TestFn Fn);
void ReportFailure(const char* File, int Line, const char* Message);
void CountCheck();
int RunAll();

struct Registrar
{
    Registrar(const char* Name, TestFn Fn) { Register(Name, Fn); }
};

} // namespace tinytest

#define TEST(Name)                                                    \
    static void Name();                                               \
    static tinytest::Registrar Registrar_##Name(#Name, &Name);        \
    static void Name()

#define CHECK(Condition)                                              \
    do                                                                \
    {                                                                 \
        tinytest::CountCheck();                                       \
        if (!(Condition))                                             \
        {                                                             \
            tinytest::ReportFailure(__FILE__, __LINE__, #Condition);  \
        }                                                             \
    } while (false)

#define CHECK_EQ(Actual, Expected)                                    \
    do                                                                \
    {                                                                 \
        tinytest::CountCheck();                                       \
        if (!((Actual) == (Expected)))                                \
        {                                                             \
            tinytest::ReportFailure(__FILE__, __LINE__,               \
                #Actual " == " #Expected);                            \
        }                                                             \
    } while (false)

#define CHECK_NEAR(Actual, Expected, Tolerance)                       \
    do                                                                \
    {                                                                 \
        tinytest::CountCheck();                                       \
        if (!(std::fabs(static_cast<double>(Actual) -                 \
                        static_cast<double>(Expected)) <=             \
              static_cast<double>(Tolerance)))                        \
        {                                                             \
            std::printf("      actual=%.17g expected=%.17g\n",        \
                static_cast<double>(Actual),                          \
                static_cast<double>(Expected));                       \
            tinytest::ReportFailure(__FILE__, __LINE__,               \
                #Actual " ~= " #Expected);                            \
        }                                                             \
    } while (false)

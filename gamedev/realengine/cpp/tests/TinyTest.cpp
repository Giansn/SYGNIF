#include "TinyTest.h"

namespace tinytest
{

namespace
{
    constexpr int MaxTests = 256;

    struct Entry
    {
        const char* Name = nullptr;
        TestFn Fn = nullptr;
    };

    // A fixed array rather than a std::vector, because registration happens
    // during static initialisation and the order in which translation units are
    // initialised is unspecified. A container that needs its own constructor to
    // have run first is a classic static-initialisation-order bug; a plain array
    // is zero-initialised before any dynamic initialiser runs.
    Entry Tests[MaxTests];
    int TestCount = 0;

    int FailureCount = 0;
    int CheckCount = 0;
    const char* CurrentTest = nullptr;
    bool bCurrentTestFailed = false;
} // namespace

void Register(const char* Name, TestFn Fn)
{
    if (TestCount < MaxTests)
    {
        Tests[TestCount].Name = Name;
        Tests[TestCount].Fn = Fn;
        ++TestCount;
    }
}

void CountCheck()
{
    ++CheckCount;
}

void ReportFailure(const char* File, int Line, const char* Message)
{
    ++FailureCount;
    bCurrentTestFailed = true;
    std::printf("  FAIL %s\n    %s:%d: %s\n", CurrentTest ? CurrentTest : "<none>", File, Line, Message);
}

int RunAll()
{
    int FailedTests = 0;

    for (int i = 0; i < TestCount; ++i)
    {
        CurrentTest = Tests[i].Name;
        bCurrentTestFailed = false;
        Tests[i].Fn();
        if (bCurrentTestFailed)
        {
            ++FailedTests;
        }
    }

    CurrentTest = nullptr;
    std::printf("\n%d tests, %d checks, %d failures (%d failing tests)\n",
        TestCount, CheckCount, FailureCount, FailedTests);

    return FailureCount == 0 ? 0 : 1;
}

} // namespace tinytest

int main()
{
    return tinytest::RunAll();
}

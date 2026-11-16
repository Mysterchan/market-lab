cmake_minimum_required(VERSION 3.16)
project(itch_replay LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
add_executable(itch_replay
    src/app/main.cpp
    src/replay/replay.cpp
    src/feed/binaryfile/reader.cpp
    src/feed/moldudp/live.cpp
    src/common/binary.cpp
    src/common/file_utils.cpp
    src/feed/moldudp/moldudp.cpp
    src/app/options.cpp
    src/feed/itch/decoder.cpp
    src/book/book.cpp
    src/output/csv.cpp
    src/output/staged_output.cpp
    src/replay/checkpoint.cpp
    src/common/digest.cpp)
target_include_directories(itch_replay PRIVATE ${CMAKE_SOURCE_DIR}/include)
target_compile_options(itch_replay PRIVATE -Wall -Wextra -Wpedantic)
add_library(itch_market SHARED src/bridge/market.cpp src/bridge/engine.cpp src/bridge/prepared.cpp
    src/feed/itch/decoder.cpp
    src/book/book.cpp src/common/binary.cpp src/feed/binaryfile/reader.cpp)
target_include_directories(itch_market PRIVATE ${CMAKE_SOURCE_DIR}/include)
target_compile_options(itch_market PRIVATE -Wall -Wextra -Wpedantic)
set_target_properties(itch_market PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)
enable_testing()
find_package(Python3 COMPONENTS Interpreter REQUIRED)
add_test(NAME replay_tests COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_replay.py $<TARGET_FILE:itch_replay>)

add_test(NAME live_tests COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_live.py $<TARGET_FILE:itch_replay>)
# Use the same complete platform discovery as Make. Tests use the source build/
# native outputs, so the supported acceptance configure command is -B build.
add_test(NAME platform_tests COMMAND ${Python3_EXECUTABLE} -m unittest discover
    -s tests/platform -p "test_*.py")
set_tests_properties(replay_tests live_tests platform_tests PROPERTIES
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR})

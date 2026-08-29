cmake_minimum_required(VERSION 3.16)
project(mj_gateway CXX C)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_C_STANDARD   11)

# ---------- Sources ----------------------------------------
set(SOURCES
    src/main.cpp
    src/tcp_forwarder.cpp
    src/packet_handlers.cpp
    src/sim_receiver.cpp
)

# real_receiver.cpp uses microjetlinux — only include if REAL_MODE enabled
option(REAL_MODE "Build with microjetlinux (requires raw socket caps)" OFF)

if(REAL_MODE)
    list(APPEND SOURCES
        src/real_receiver.cpp
        microjetlinux/microjet_driver.c
        microjetlinux/ptp.c
        microjetlib/mon/mj_mon_spec.c
        microjetlib/src/mjtypes.c
        microjetlib/src/mjutils.c
    )
    add_compile_definitions(REAL_MODE_ENABLED)
endif()

add_executable(mj_gateway ${SOURCES})

# ---------- Includes ----------------------------------------
target_include_directories(mj_gateway PRIVATE
    src
    microjetlib/inc
    microjetlib/mon
    microjetlinux
)

# ---------- Link --------------------------------------------
target_link_libraries(mj_gateway pthread)

# ---------- Compiler flags ----------------------------------
target_compile_options(mj_gateway PRIVATE
    -Wall -Wextra -O2
    -Wno-unused-parameter
)

# ---------- Tests ------------------------------------------
include(CTest)
if(BUILD_TESTING)
    add_executable(gateway_protocol_tests
        tests/protocol_tests.cpp
        src/packet_handlers.cpp
        src/tcp_forwarder.cpp
    )
    target_include_directories(gateway_protocol_tests PRIVATE src)
    target_link_libraries(gateway_protocol_tests pthread)
    target_compile_options(gateway_protocol_tests PRIVATE -Wall -Wextra)
    add_test(NAME gateway_protocol_tests COMMAND gateway_protocol_tests)
endif()

# ---------- Defines -----------------------------------------
# Режим по умолчанию задаётся через env GATEWAY_MODE, не через compile-time
# (позволяет менять без перекомпиляции через docker-compose env)

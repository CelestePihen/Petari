#pragma once

#include <revolution/types.h>

inline void memoryBarrier() {
    asm {
        sync
    }
}

template < typename T, unsigned int Capacity >
struct EventQueue {
    volatile u32 writeCount;
    volatile u32 readCount;
    T events[Capacity];

    bool push(const T& event);
    bool pop(T& event);
    bool isEmpty() const;
    bool isFull() const;
};

template < typename T, unsigned int Capacity >
bool EventQueue< T, Capacity >::push(const T& event) {
    if (Capacity <= 0)
        return false;

    if (isFull())
        return false;

    events[writeCount % Capacity] = event;

    memoryBarrier();

    ++writeCount;

    return true;
}

template < typename T, unsigned int Capacity >
bool EventQueue< T, Capacity >::pop(T& event) {
    if (Capacity <= 0)
        return false;

    if (isEmpty())
        return false;

    event = events[readCount % Capacity];

    memoryBarrier();

    ++readCount;

    return true;
}

template < typename T, unsigned int Capacity >
bool EventQueue< T, Capacity >::isEmpty() const {
    return writeCount == readCount;
}

template < typename T, unsigned int Capacity >
bool EventQueue< T, Capacity >::isFull() const {
    return (writeCount - readCount) == Capacity;
}

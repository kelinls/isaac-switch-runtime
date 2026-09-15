#pragma once

enum class RuntimeState {
    Cold,
    WaitingForModule,
    Ready,
    Disabled,
};

struct RuntimeContext {
    bool titleChecked = false;
    bool titleOk = false;
    bool fatalFailure = false;
    bool moduleFound = false;
    bool buildMismatch = false;
    bool hookAttempted = false;
    bool hookSucceeded = false;
};

RuntimeState Step(RuntimeState state, const RuntimeContext& context);

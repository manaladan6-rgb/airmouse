AirMouse v1.0.0
===============
Physics-driven webcam finger mouse.

Control your cursor with hand gestures using pure spring-damper physics.
Your cursor has MASS, is pulled by a SPRING toward your finger, and
slowed by DAMPING — it accelerates naturally, overshoots slightly,
then settles. Pure Newtonian mechanics. No hacks.

Install:
    pip install airmouse-1.0.0-py3-none-any.whl

Run:
    airmouse

Controls:
    Index finger up     Move cursor
    Pinch (thumb+index) Left click
    Middle finger up    Right click
    q                   Quit
    d                   Toggle debug overlay
    r                   Recalibrate

Tune physics:
    airmouse --sensitivity 1.5 --mass 1.2 --stiffness 200 --damping 26

#ifndef DimpleOpticalDiagnostics_h
#define DimpleOpticalDiagnostics_h 1

#include "globals.hh"

class DetectorConstruction;
class G4Event;
class G4Step;
class G4Track;

// Passive, default-off event counters for the single-tile engineering check.
// No terminal-fate accounting, geometry queries, random draws, or track changes.
namespace DimpleOpticalDiagnostics
{
  void BeginRun(const DetectorConstruction* detector);
  void BeginEvent(const G4Event* event);
  void ObserveStep(const G4Step* step);
  void ObserveTrack(const G4Track* track);
  void ObserveDetection(const G4Track* track);
  void EndEvent(const G4Event* event, G4int legacyGenerated, G4int legacyDetected);
}

#endif

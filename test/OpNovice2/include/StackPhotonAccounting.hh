#ifndef StackPhotonAccounting_h
#define StackPhotonAccounting_h 1

#include "globals.hh"

#include <array>
#include <map>
#include <tuple>
#include <unordered_map>
#include <vector>

class DetectorConstruction;
class G4Event;
class G4OpBoundaryProcess;
class G4Step;
class G4Track;
class G4VPhysicalVolume;
class Run;

// Independent stack-v2 accounting. This class never changes particle/step
// state, process activation, geometry, weights, or random-engine state.
class StackPhotonAccounting
{
  public:
    enum VolumeClass { kUnknown = 0, kTile = 1, kSteel = 2, kWorld = 3, kSiPM = 4 };
    enum Fate {
      kCollected = 0, kBulkTile = 1, kBulkSteel = 2, kBulkWorld = 3,
      kBulkOther = 4, kBoundaryAbsorption = 5, kBoundaryDetection = 6,
      kWorldEscape = 7, kWLS = 8, kWLS2 = 9, kNoRindex = 10,
      kOtherTermination = 11, kFateCount = 12
    };
    explicit StackPhotonAccounting(const DetectorConstruction& detector);
    static void BookNtuples();
    void BeginEvent();
    void ObservePrimaries(const G4Event* event);
    void BeginTracking(const G4Track* track);
    void ObserveStep(const G4Step* step);
    void ObserveSiPMCollection(const G4Step* step);
    void EndTracking(const G4Track* track);
    void WriteEvent(const Run& run);

  private:
    struct Source {
      G4int volumeClass = kUnknown;
      G4int layer = -1;
      G4int sensor = -1;
    };
    struct Birth {
      Source root;
      Source location;
      G4String creator;
      G4int parentID = -1;
      G4bool opticalParent = false;
      G4bool primary = false;
    };
    struct BirthCounts {
      G4int total = 0, primary = 0, nonOpticalParent = 0, opticalParent = 0;
      G4int scintillation = 0, cerenkov = 0, wls = 0, wls2 = 0, other = 0;
      void Add(const Birth& birth);
    };
    struct TrackRecord {
      Birth birth;
      G4bool started = false, finalized = false, worldEscape = false;
      G4int lastStep = -1, boundaryStatus = -1, collectedSensor = -1;
      G4bool collected = false;
      Source preVolume;
      G4String process;
    };
    struct LayerRecord {
      BirthCounts births;
      G4int detectedAll = 0, detectedSameRoot = 0, detectedSameBirth = 0;
      G4double tileNonOpticalEdep = 0., tileOpticalEdep = 0.;
      G4double steelNonOpticalEdep = 0., steelOpticalEdep = 0.;
    };
    struct SensorRecord {
      G4int layer = -1, local = -1, copy = -1;
      G4int all = 0, sameRoot = 0, sameBirth = 0;
      G4int rootOutside = 0, rootUnknown = 0, birthOutside = 0, birthUnknown = 0;
    };
    using FlowKey = std::tuple<G4int, G4int, G4int, G4int, G4int, G4int,
                               G4String, G4int, G4int, G4int>;

    Source Locate(const G4VPhysicalVolume* volume) const;
    void RegisterBirth(const Birth& birth);
    void ObserveSecondaryBirths(const G4Step* step);
    G4OpBoundaryProcess* BoundaryProcess();

    G4int fLayerCount = 0, fSensorsPerLayer = 0, fCopyStride = 0;
    std::unordered_map<const G4VPhysicalVolume*, Source> fVolumes;
    std::vector<SensorRecord> fSensors;
    std::unordered_map<G4int, std::size_t> fSensorByCopy;
    std::vector<LayerRecord> fLayers;
    std::unordered_map<const G4Track*, Birth> fPendingBirths;
    std::unordered_map<G4int, TrackRecord> fTracks;
    std::unordered_map<G4int, G4int> fNonOpticalLastStep;
    std::map<FlowKey, G4int> fFlows;
    BirthCounts fBirths;
    std::array<G4int, 5> fBirthVolumes{};
    std::array<G4int, kFateCount> fFates{};
    G4int fStarted = 0, fFinalized = 0, fExpectedPrimaryBirths = 0, fPrimaryStarted = 0;
    G4int fUnmatchedStarts = 0, fDuplicateBirths = 0, fDuplicateStarts = 0;
    G4int fDuplicateFinalizations = 0, fParentMismatches = 0;
    G4int fUnknownBirthOrigins = 0, fUnknownRootOrigins = 0;
    G4int fInactiveSensorHits = 0, fDuplicateSteps = 0, fBoundaryTechnical = 0;
    G4int fBoundaryNoRindex = 0, fCollectedStepNoRindex = 0;
    G4bool fWritten = false, fBoundarySearched = false;
    G4OpBoundaryProcess* fBoundary = nullptr;
};

#endif

#ifndef W08PhotonDiagnostics_h
#define W08PhotonDiagnostics_h 1

#include "G4ThreeVector.hh"
#include "globals.hh"

#include <array>
#include <fstream>
#include <map>
#include <string>
#include <unordered_map>

class G4OpBoundaryProcess;
class G4Event;
class G4Step;
class G4Track;
class G4VPhysicalVolume;

// A read-only snapshot of the existing W08 geometry. No diagnostic volumes,
// surfaces, navigation queries, or sampling are used by the observer.
struct W08GeometrySnapshot
{
  G4bool enabled = false;
  G4bool dimple = false;
  G4ThreeVector worldHalfSize;
  G4ThreeVector tileHalfSize;
  G4ThreeVector tileCenter;
  G4ThreeVector sipmHalfSize;
  G4ThreeVector sipmCenter;
  G4ThreeVector dimpleCenter;
  G4double dimpleRadius = 0.;
  const G4VPhysicalVolume* worldPV = nullptr;
  const G4VPhysicalVolume* tilePV = nullptr;
  const G4VPhysicalVolume* sipmPV = nullptr;
};

class W08PhotonDiagnostics
{
  // The live diagnostic guard accepts the corrected W08 baseline: electron
  // scintillation active, optical-photon scintillation explicitly inactive.
  // That particle-specific change belongs to the initialization macro; this
  // class remains an observer. Frozen legacy/debug candidates are separate.
  public:
    enum Fate {
      kCollected = 0, kTileAbsorption = 1, kAirAbsorption = 2,
      kOtherAbsorption = 3, kBoundaryAbsorption = 4,
      kBoundaryDetection = 5, kWorldEscape = 6,
      kWLSConversion = 7, kOtherTermination = 8, kFateCount = 9
    };
    enum Region {
      kNone = 0, kTileTop = 1, kTileBottom = 2,
      kTileXMinus = 3, kTileXPlus = 4, kTileYMinus = 5, kTileYPlus = 6,
      kDimple = 7, kSiPMFront = 8, kSiPMBack = 9,
      kSiPMXMinus = 10, kSiPMXPlus = 11, kSiPMYMinus = 12, kSiPMYPlus = 13,
      kWorld = 14, kEdge = 15, kUnknown = 16
    };
    enum Interface {
      kNoInterface = 0, kTileAir = 1, kTileSiPM = 2,
      kAirSiPM = 3, kWorldInterface = 4, kOtherInterface = 5
    };

    explicit W08PhotonDiagnostics(const W08GeometrySnapshot& geometry);
    static void BookNtuples();

    void SetSourceIsElectron(G4bool value) { fSourceIsElectron = value; }
    void BeginEvent();
    void ObservePrimary(const G4Event* event);
    void BeginTracking(const G4Track* track);
    void ObserveStep(const G4Step* step);
    void ObserveSiPMCollection(const G4Step* step);
    void EndTracking(const G4Track* track);
    void WriteEvent(G4int runID, G4int eventID, G4int generatedReference);

  private:
    struct TrackRecord {
      G4bool started = false;
      G4bool finalized = false;
      G4bool collected = false;
      G4bool everDimple = false;
      G4bool everAmbiguous = false;
      G4bool worldEscape = false;
      G4int lastExitRegion = kNone;
      G4int lastObservedStep = -1;
      G4int boundaryStatus = -1;
      const G4VPhysicalVolume* preVolume = nullptr;
      G4String processName;
    };
    struct Surface {
      G4int region = kUnknown;
      G4ThreeVector outwardNormal;
    };
    struct PendingBirth {
      G4int serial = 0;
      G4int parentID = 0;
    };
    using BoundaryKey = std::array<G4int, 5>;
    using TerminalKey = std::array<G4int, 3>;

    Surface ClassifyBox(const G4ThreeVector& point,
                        const G4ThreeVector& center,
                        const G4ThreeVector& halfSize,
                        G4bool tile) const;
    Surface ClassifyTile(const G4ThreeVector& point) const;
    void ObserveBoundary(const G4Step* step, TrackRecord& record);
    G4OpBoundaryProcess* BoundaryProcess();
    G4int IncidenceBin(const G4ThreeVector& momentum,
                       const G4ThreeVector& normal);
    void ObserveBirths(const G4Step* step);
    void BindBirth(const G4Track* track);
    void WriteBirthAuditRow(const std::array<std::string, 24>& values);
    void WriteBirthAuditSummary(G4int eventID, G4int generatedReference);

    W08GeometrySnapshot fGeometry;
    G4double fRegionTolerance;
    G4bool fSourceIsElectron = true;
    G4bool fEventWritten = false;
    G4OpBoundaryProcess* fBoundaryProcess = nullptr;
    G4bool fBoundaryProcessSearched = false;
    std::unordered_map<G4int, TrackRecord> fTracks;
    std::map<BoundaryKey, G4int> fBoundaryCounts;
    std::map<TerminalKey, G4int> fTerminalCounts;
    std::array<G4int, kFateCount> fFates{};
    // front, back, -X, +X, -Y, +Y, edge/unknown.
    std::array<G4int, 7> fSiPMFaces{};
    G4int fStarted = 0;
    G4int fFinalized = 0;
    G4int fDuplicateFinalizations = 0;
    G4int fUnknownRegions = 0;
    G4int fAmbiguousBoundaries = 0;
    G4int fInvalidNormals = 0;
    G4int fBoundaryTechnical = 0;
    G4int fUnsupportedBirths = 0;
    G4double fNonOpticalEnergyDeposit = 0.;
    G4double fOpticalEnergyDeposit = 0.;
    // This optional forensic output never participates in physics or the
    // production counters. Pending pointers are removed as soon as the actual
    // track ID is assigned, so allocator address reuse cannot merge births.
    std::ofstream fBirthAudit;
    std::unordered_map<const G4Track*, PendingBirth> fPendingBirths;
    G4int fBirthAuditEventID = -1;
    G4int fNextBirthSerial = 0;
    G4int fNonOpticalParentBirths = 0;
    G4int fOpticalParentBirths = 0;
    G4int fUnmatchedBirthBindings = 0;
    G4int fDuplicateBirths = 0;
    G4int fBirthParentMismatches = 0;
    G4int fLinkedStarts = 0;
};

#endif
